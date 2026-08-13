"""
PyMOL MCP Plugin

A plugin that listens for socket connections and executes allowlisted PyMOL
commands received as structured JSON. No arbitrary code execution (exec) is used.

Based on the concept of the "Rendering Plugin" from Michael Lerner.
"""

from __future__ import absolute_import, print_function

import ast
import hashlib
import json
import math
import os
import socket
import tempfile
import threading
import time
import traceback
import zipfile
from collections import deque

# Global variables
socket_server = None
# Bounded: nothing reads this back, and an unbounded list would grow for the
# life of the PyMOL process. The on-disk history below is the durable record.
received_commands = deque(maxlen=200)
current_port = 9876  # Default port
# Each PyMOL claims the first free port here, so several can run at once. The
# MCP server discovers them by scanning the same range, which needs no registry
# file and cannot go stale when an instance is killed.
PORT_RANGE = range(9876, 9896)
_dispatcher = None  # Built lazily on first command

# PyMOL is commonly launched from the same terminal as the MCP client. Anything
# printed from this plugin's socket thread goes to that terminal and interleaves
# with the client's own screen drawing, corrupting its display. Command errors
# reach the client as tool results regardless, so stay quiet unless
# PYMOL_MCP_VERBOSE is set in the environment PyMOL was started with.
VERBOSE = bool(os.environ.get("PYMOL_MCP_VERBOSE", "").strip())


def _log(message):
    """Print only when PYMOL_MCP_VERBOSE is set -- see the note above."""
    if VERBOSE:
        print(message)


##############################################################################
# COMMAND HISTORY
##############################################################################

# Commands are written to disk as they run, so a session survives PyMOL exiting
# or crashing. Two files, because they answer different questions:
#
#   history.jsonl   every command with its arguments and outcome, for working
#                   out what went wrong
#   session-*.pml   validated state-changing commands only, as literal PyMOL
#                   syntax, replayed from a clean state with `@session-....pml`
#
# Set PYMOL_MCP_HISTORY to another directory, or to "off" to disable.
HISTORY_SETTING = os.environ.get("PYMOL_MCP_HISTORY", "").strip()
HISTORY_OFF = ("off", "0", "false", "no")

# Commands that touch the filesystem, and the argument holding the path.
# Recorded absolute: PyMOL resolves a relative path against its own working
# directory, which cannot be recovered from the history afterwards. `fetch`
# is not here because its argument is a PDB code, not a path; PyMOL decides
# where the download lands.
FILE_ARGS = {
    "load": ("filename", "in"),
    "save": ("filename", "out"),
    "save_file": ("filename", "out"),
    "png": ("filename", "out"),
}

# Commands kept out of the history. Reading the history is not part of the
# session being recorded, and recording it would mean every poll pushes the
# commands the caller is looking for further out of reach.
HISTORY_EXCLUDED = frozenset({"get_history", "export_session"})

# Bit index per representation, mirroring pymol.viewing.repres. The `reps`
# field in a cmd.iterate namespace is this bitmask.
#
# Copied rather than imported so the handler stays testable without PyMOL.
# TestRepresentationBits in the integration suite asserts this still equals
# viewing.repres, which is the only thing standing between it and rot.
#
# Split by whether the bit can appear per atom, established by showing each
# representation alone on a fragment and reading the mask back. The object
# level ones always read 0 there, so they have to be reported as unknowable
# rather than as absent. `everything` (-1) is a composite and excluded, as are
# the composites in viewing.repmasks (licorice 17, wire 2176).
ATOM_LEVEL_REPS = {
    "sticks": 0,
    "spheres": 1,
    "surface": 2,
    "labels": 3,
    "nb_spheres": 4,
    "cartoon": 5,
    "ribbon": 6,
    "lines": 7,
    "mesh": 8,
    "dots": 9,
    "nonbonded": 11,
    "ellipsoids": 19,
}
OBJECT_LEVEL_REPS = {
    "dashes": 10,
    "cell": 12,
    "cgo": 13,
    "callback": 14,
    "extent": 15,
    "slice": 16,
    "angles": 17,
    "dihedrals": 18,
    "volume": 20,
}
REP_BITS = dict(ATOM_LEVEL_REPS, **OBJECT_LEVEL_REPS)

_history_dir = None  # resolved on the first recorded command
_history_pml = None  # this session's replay script
_history_lock = threading.Lock()
_history_broken = False  # set after a write failure; stops retrying


def _history_directory():
    """Resolve the history directory without creating anything. None if off.

    Split out from _history_paths so a reader can find the directory without
    the side effects of a writer. _history_paths writes a session-*.pml header
    as soon as it is called, so a read built on it would fabricate a replay
    script in a PyMOL that had run nothing -- the tool that answers "what did I
    run" would answer by creating a file.
    """
    if _history_dir is not None:
        return _history_dir
    if HISTORY_SETTING.lower() in HISTORY_OFF:
        return None
    return HISTORY_SETTING or os.path.join(os.path.expanduser("~"), ".pymol-mcp")


def _history_paths():
    """Resolve and create the history directory. Returns (None, None) if off."""
    global _history_dir, _history_pml

    if _history_dir is not None:
        return _history_dir, _history_pml

    directory = _history_directory()
    if directory is None:
        return None, None

    os.makedirs(directory, exist_ok=True)

    session_id = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())
    pml = os.path.join(directory, "session-%s.pml" % session_id)
    with open(pml, "a") as fh:
        fh.write("# pymol-mcp session %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        # Any relative path below was resolved against this directory, so a
        # replay from elsewhere needs it.
        fh.write("# PyMOL working directory: %s\n" % os.getcwd())
        fh.write("# Replay with:  @%s\n\n" % pml)
        # A replay must not inherit objects, settings, selections or a camera
        # from whichever PyMOL window happens to execute it.
        fh.write("reinitialize\n")

    _history_dir, _history_pml = directory, pml
    _log("Recording command history to %s" % directory)
    return _history_dir, _history_pml


def _replay_source(command_name, replay, args):
    """Return validated, canonical replay syntax or None.

    Audit provenance and replay syntax are separate protocol fields. Deriving
    one from the other made typed calls such as ``count {'selection': 'all'}``
    look replayable even though that is not PyMOL command syntax.
    """
    if not isinstance(replay, str):
        return None
    replay = replay.strip()
    if not replay or not replay.isprintable() or ";" in replay or replay.endswith("\\"):
        return None
    expected = "save" if command_name == "save_file" else command_name.lower()
    if replay.split(None, 1)[0].lower() != expected:
        return None

    # Input paths are resolved by the PyMOL process, not the MCP server. Make
    # loads independent of the directory from which a later replay is run.
    if command_name == "load":
        filename = args.get("filename")
        if filename:
            replay = "load %s" % os.path.abspath(os.path.expanduser(str(filename)))
            if args.get("object"):
                replay += ", %s" % args["object"]
            if args.get("options"):
                replay += ", %s" % args["options"]
    elif command_name == "save":
        filename = os.path.abspath(os.path.expanduser(str(args["filename"])))
        selection = args.get("selection") or "(all)"
        if selection == "all":
            selection = "(all)"
        replay = "save %s, %s, %s" % (
            filename,
            selection,
            args.get("state", -1),
        )
    elif command_name == "png":
        # Typed rendering executes against an atomic temporary path but sends
        # the final deliverable path in replay. Canonicalise that path rather
        # than the executed filename from args.
        command, separator, options = replay.partition(",")
        filename = command[len("png ") :].strip()
        replay = "png %s" % os.path.abspath(os.path.expanduser(filename))
        if separator:
            replay += "," + options
    elif command_name == "set_view":
        view = _parse_view(args.get("view"))
        replay = "set_view (%s)" % ",".join(str(float(item)) for item in view)
    elif command_name == "save_file":
        filename = os.path.abspath(os.path.expanduser(str(args["filename"])))
        selection = args.get("selection") or "(all)"
        replay = "save %s, %s, %s" % (
            filename,
            selection,
            args.get("state", -1),
        )
    return replay


def _record_history(command_name, args, source, result, replay=None):
    """Append one command to the history files.

    Never raises. A history write failing must not stop PyMOL executing
    commands, so any error disables recording for the rest of the session
    rather than propagating to the caller.
    """
    global _history_broken

    # No source means an internal call, currently the connection health-check
    # ping, which is not part of the user's session.
    if _history_broken or not source or command_name in HISTORY_EXCLUDED:
        return

    try:
        with _history_lock:
            directory, pml = _history_paths()
            if directory is None:
                return

            ok = not (isinstance(result, dict) and result.get("executed") is False)
            if command_name == "clear_selections" and replay == "clear_selections":
                data = result.get("data", {}) if isinstance(result, dict) else {}
                replay_sources = [
                    "delete %s" % name for name in data.get("deleted", [])
                ]
                replay_sources.append("deselect")
            else:
                validated = _replay_source(command_name, replay, args)
                replay_sources = [validated] if validated is not None else []
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "session_id": os.path.basename(pml)[len("session-") : -len(".pml")],
                "command": command_name,
                "args": args,
                "source": source,
                "ok": ok,
                "replayable": bool(replay_sources),
            }
            if replay_sources:
                record["replay"] = (
                    replay_sources[0] if len(replay_sources) == 1 else replay_sources
                )
            if isinstance(result, dict):
                detail = result.get("error") if not ok else result.get("output")
                if detail:
                    # util.cbc and friends can return a lot; keep the file sane.
                    record["error" if not ok else "output"] = str(detail)[:2000]

            spec = FILE_ARGS.get(command_name)
            if spec:
                arg_name, direction = spec
                path = args.get(arg_name)
                if command_name == "png" and replay_sources:
                    path = replay_sources[0].partition(",")[0][len("png ") :]
                if path:
                    record["file"] = {
                        "path": os.path.abspath(os.path.expanduser(str(path))),
                        "direction": direction,
                    }

            with open(os.path.join(directory, "history.jsonl"), "a") as fh:
                fh.write(json.dumps(record) + "\n")

            # A failed command would not replay, so keep the script clean.
            if ok and replay_sources:
                with open(pml, "a") as fh:
                    for replay_source in replay_sources:
                        fh.write(replay_source + "\n")
    except Exception as e:
        _history_broken = True
        _log("Command history disabled after a write error: %s" % e)


def _record_event(kind, detail):
    """Append a non-command event to history.jsonl. Never raises."""
    global _history_broken

    if _history_broken:
        return
    try:
        with _history_lock:
            directory, pml = _history_paths()
            if directory is None:
                return
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "session_id": os.path.basename(pml)[len("session-") : -len(".pml")],
                "event": kind,
                "detail": detail,
                "ok": False,
            }
            with open(os.path.join(directory, "history.jsonl"), "a") as fh:
                fh.write(json.dumps(record) + "\n")
    except Exception as e:
        _history_broken = True
        _log("Command history disabled after a write error: %s" % e)


def _report_listener_death(port, error):
    """Announce that the listener stopped without being asked to.

    Deliberately not routed through _log. VERBOSE exists because stray prints
    from the socket thread corrupt the MCP client's terminal, and the reasoning
    was that command errors reach the client as tool results anyway. That does
    not hold here: a listener that has died cannot report itself through the
    socket, and every client-side symptom ("no PyMOL is listening") is
    indistinguishable from PyMOL having been closed. This is once per session.
    """
    reason = f": {error}" if error else " unexpectedly"
    message = (
        f"MCP socket listener on port {port} stopped{reason}. "
        "PyMOL is still running; call start_socket_server() to restart it."
    )
    print(message)
    if error and VERBOSE:
        traceback.print_exc()
    _record_event("listener_died", message)


##############################################################################
# SERVER CONTROL
##############################################################################


def execute_structured_command(command_name, args):
    """
    Execute an allowlisted PyMOL command via the dispatcher.
    No exec() or eval() — only direct cmd.* function calls.
    """
    global _dispatcher

    try:
        _log(f"Executing PyMOL command: {command_name} args={args}")

        if _dispatcher is None:
            from pymol import cmd

            _dispatcher = build_command_dispatcher(cmd)

        handler = _dispatcher.get(command_name)
        if handler is None:
            error_msg = f"Unknown command: {command_name}"
            _log(error_msg)
            return {"executed": False, "error": error_msg}

        import io
        from contextlib import redirect_stdout

        output_buffer = io.StringIO()
        with redirect_stdout(output_buffer):
            result = handler(args)

        output = output_buffer.getvalue()

        # Introspection handlers return JSON-serialisable structures. They go
        # back in their own field, untouched: str(result) would hand the server
        # a Python repr to parse, which is precisely the string round-trip the
        # typed tools exist to remove.
        if isinstance(result, (dict, list)):
            payload = {"executed": True, "data": result}
            if output:
                payload["output"] = output
            return payload

        if output:
            _log(f"Command output: {output}")
            return {"executed": True, "output": output}
        elif result is not None:
            return {"executed": True, "output": str(result)}
        else:
            return {
                "executed": True,
                "output": "Command executed successfully (no output)",
            }
    except Exception as e:
        error_msg = f"Error executing PyMOL command '{command_name}': {str(e)}"
        _log(error_msg)
        if VERBOSE:
            traceback.print_exc()
        return {"executed": False, "error": error_msg}


def start_socket_server(port=None):
    """
    Start the MCP socket listener. Returns True if it was started by this call,
    False if no port could be claimed. Safe to call from `.pymolrc.py`.

    With no port, claims the first free one in PORT_RANGE, so a second PyMOL
    gets its own listener instead of silently having none. The MCP server finds
    them by scanning the same range. Pass a port explicitly to pin one.
    """
    global socket_server, current_port

    if is_listening():
        return False

    candidates = [port] if port else list(PORT_RANGE)
    for candidate in candidates:
        server = SocketServer(port=candidate)
        if server.start(execute_structured_command):
            socket_server = server
            current_port = candidate
            return True

    return False


def describe_instance(port):
    """Identify this PyMOL to a client scanning for instances.

    A bare port number tells nobody which window they are about to drive, so
    report the loaded objects too. Everything here is best-effort: discovery
    must not fail because one field could not be read.
    """
    info = {"executed": True, "port": port, "pid": os.getpid()}
    try:
        from pymol import cmd

        info["objects"] = list(cmd.get_names("objects"))
    except Exception as e:  # noqa: BLE001 - report, never propagate
        info["objects"] = []
        info["warning"] = f"could not list objects: {e}"
    return info


def is_listening():
    """True when a listener thread is actually running.

    Derived from the server rather than tracked in a separate flag. A tracked
    flag desynced when the accept loop died: it stayed True, so
    start_socket_server refused to restart ("returns False and does nothing,
    so it looks like it worked") and the dialog showed green while nothing was
    bound. Asking the server cannot drift.
    """
    return socket_server is not None and socket_server.running


def stop_socket_server():
    """Stop the MCP socket listener if it is running."""
    if socket_server is not None:
        socket_server.stop()


##############################################################################
# ATOM EXPRESSION SAFETY
##############################################################################

# `alter` and `alter_state` hand their expression to PyMOL, which evaluates it
# as Python once per atom. That is a genuine eval: verified that
# `alter all, __import__('pathlib').Path('/tmp/x').write_text('x')` writes the
# file. Every other command in the dispatcher is a fixed cmd.* call with string
# arguments, so these two are the only way to get code executed, and this is
# where that is stopped.
#
# Enforced here rather than in the MCP server because the socket is the real
# boundary: anything on the machine can connect and send a command without
# going through the server at all.

# Per-atom properties PyMOL exposes to the expression namespace.
ALTER_NAMES = frozenset(
    {
        "name",
        "resn",
        "resi",
        "resv",
        "chain",
        "segi",
        "elem",
        "alt",
        "b",
        "q",
        "type",
        "formal_charge",
        "partial_charge",
        "numeric_type",
        "text_type",
        "vdw",
        "ss",
        "color",
        "label",
        "ID",
        "index",
        "rank",
        "model",
        "state",
        "cartoon",
        "flags",
        "geom",
        "valence",
        "protons",
        "oneletter",
        "reps",
        "True",
        "False",
        "None",
    }
)
ALTER_COORDINATE_NAMES = frozenset({"x", "y", "z"})
# Calls are otherwise blocked outright; these are pure and cannot reach out.
ALTER_CALLS = frozenset({"str", "int", "float", "abs", "round", "len", "min", "max"})

# Anything not listed is rejected, so new syntax fails closed. Attribute and
# Subscript are deliberately absent: they are what make `().__class__` and
# similar sandbox escapes work.
ALTER_NODES = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Call,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.Tuple,
    ast.List,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
)


def _reject_control_characters(value):
    """Refuse newlines and control characters in a selection.

    Nothing in the dispatcher should ever build a PyMOL command string, but a
    stray newline reaching one that does turns a selection into a second
    command. Cheap to enforce here so a future handler cannot reintroduce it.
    """
    if not isinstance(value, str):
        return
    bad = [c for c in value if c in "\r\n" or (ord(c) < 32 and c != "\t")]
    if bad:
        raise ValueError("selection may not contain newlines or control characters")


def _check_setting_name(name):
    """Validate a PyMOL setting name and return it.

    Shared by every handler that takes a setting name. A name reaches PyMOL as
    a lookup key, never as part of an expression, and this keeps it that way.
    """
    if (
        not isinstance(name, str)
        or not name
        or (not name[0].isalpha() and name[0] != "_")
        or not all(character.isalnum() or character == "_" for character in name)
    ):
        raise ValueError("invalid setting name")
    return name


def _atom_scope(selection):
    r"""Wrap a selection so it addresses the atom layer, not the object layer.

    PyMOL settings live in three layers -- global, per-object, per-atom -- and
    punctuation alone decides which one `set` and `unset` write to. A bare
    identifier addresses the object layer; anything parenthesised or compound
    addresses the atoms. Measured, with a global of 0.6 and an atom override of
    0.8 on `ala and name CA`:

        unset via 'ala'               0.80 -> 0.80   silently does nothing
        unset via 'all'               0.80 -> 0.80   silently does nothing
        unset via '(ala)'             0.80 -> 0.60   cleared
        unset via '(all)'             0.80 -> 0.60   cleared
        unset via 'ala and name CA'   0.80 -> 0.60   cleared

    Both failing forms report success, so the caller cannot tell. Wrapping is
    the whole fix, and it is why the typed tools send an explicit scope rather
    than letting a selection's shape decide.
    """
    return "(%s)" % selection


def _parse_png_options(options):
    """Parse safe ``cmd.png`` keyword or positional arguments."""
    if options is None or not str(options).strip():
        return {}

    text = str(options).strip()
    _reject_control_characters(text)
    positional_names = ("width", "height", "dpi", "ray", "quiet")
    converters = {
        "width": int,
        "height": int,
        "dpi": float,
        "ray": int,
        "quiet": int,
    }
    parsed = {}
    positional_index = 0

    for raw_part in text.split(","):
        part = raw_part.strip()
        if not part:
            raise ValueError("PNG options must not contain empty arguments")
        if "=" in part:
            name, value = (piece.strip() for piece in part.split("=", 1))
            if name not in converters:
                raise ValueError("unsupported PNG option: %s" % name)
        else:
            if positional_index >= len(positional_names):
                raise ValueError("too many positional PNG options")
            name = positional_names[positional_index]
            value = part
            positional_index += 1

        if name in parsed:
            raise ValueError("duplicate PNG option: %s" % name)
        try:
            parsed[name] = converters[name](value)
        except (TypeError, ValueError):
            raise ValueError("invalid value for PNG option %s: %s" % (name, value))

    width = parsed.get("width", 0)
    height = parsed.get("height", 0)
    if not 0 <= width <= 10_000 or not 0 <= height <= 10_000:
        raise ValueError("PNG width and height must be between 0 and 10000")
    if width and height and width * height > 64_000_000:
        raise ValueError("PNG output may not exceed 64 megapixels")
    if "dpi" in parsed:
        dpi = parsed["dpi"]
        if not math.isfinite(dpi) or (dpi != -1 and not 1 <= dpi <= 2400):
            raise ValueError("PNG dpi must be -1 or between 1 and 2400")
    if "ray" in parsed and parsed["ray"] not in (0, 1):
        raise ValueError("PNG ray must be 0 or 1")
    if "quiet" in parsed and parsed["quiet"] not in (0, 1):
        raise ValueError("PNG quiet must be 0 or 1")
    return parsed


def _parse_view(value):
    """Validate a camera view without evaluating caller-provided Python."""
    if isinstance(value, str):
        _reject_control_characters(value)
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            raise ValueError("view must be a JSON list of 18 numbers")
    if not isinstance(value, (list, tuple)) or len(value) != 18:
        raise ValueError("view must contain exactly 18 numbers")
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        raise ValueError("view must contain only numbers")
    if not all(math.isfinite(item) for item in result):
        raise ValueError("view values must be finite")
    return result


def check_atom_expression(expression, coordinates=False):
    """Raise ValueError unless the expression is a safe atom-property formula.

    Allows arithmetic, comparisons, conditionals and a few pure calls over
    PyMOL's per-atom properties. Rejects attribute access, subscripting,
    lambdas, comprehensions and any call that is not on the short list.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("alter expression must be a non-empty string")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"alter expression is not a valid expression: {e}")

    allowed = ALTER_NAMES | ALTER_CALLS
    if coordinates:
        allowed = allowed | ALTER_COORDINATE_NAMES

    for node in ast.walk(tree):
        if not isinstance(node, ALTER_NODES):
            raise ValueError(
                f"{type(node).__name__} is not allowed in an alter expression; "
                "only arithmetic over atom properties is permitted"
            )
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALTER_CALLS:
                raise ValueError(
                    "only these calls are allowed in an alter expression: "
                    + ", ".join(sorted(ALTER_CALLS))
                )
        if isinstance(node, ast.Name) and node.id not in allowed:
            raise ValueError(
                f"'{node.id}' is not a known atom property; allowed names are: "
                + ", ".join(sorted(allowed))
            )


##############################################################################
# ALLOWLISTED COMMAND DISPATCH
##############################################################################


def build_command_dispatcher(cmd):
    """
    Build a dispatcher dict mapping command names to handler functions.
    Each handler calls the corresponding cmd.* function with validated args.
    Returns only allowlisted PyMOL API calls — no exec() or eval().
    """

    def _util_command(command_name, args):
        """Call a pymol.util function directly.

        These used to be assembled into a string and handed to cmd.do(), which
        is PyMOL's full command interpreter: a newline in the selection let a
        caller append `run /path/evil.pml` and have it executed. Calling the
        function removes the interpreter from the path entirely.
        """
        import inspect

        from pymol import util

        name = command_name.split(".", 1)[1]
        func = getattr(util, name, None)
        if not callable(func):
            raise ValueError(f"{command_name} is not available in this PyMOL")

        selection = args.get("selection") or "all"
        _reject_control_characters(selection)

        # PyMOL's interpreter calls these with quiet=0; the Python default is
        # quiet=1. util.cbc's chain listing is the only way to enumerate chains
        # through this server, so the output has to be preserved.
        if "quiet" in inspect.signature(func).parameters:
            return func(selection, quiet=0)
        return func(selection)

    def _show(args):
        return cmd.show(
            args.get("representation", "lines"), args.get("selection", "all")
        )

    def _hide(args):
        return cmd.hide(
            args.get("representation", "lines"), args.get("selection", "all")
        )

    def _color(args):
        return cmd.color(args.get("color", "white"), args.get("selection", "all"))

    def _bg_color(args):
        return cmd.bg_color(args.get("color", "black"))

    def _as(args):
        return cmd.show_as(
            args.get("representation", "cartoon"), args.get("selection", "all")
        )

    def _set(args):
        selection = args.get("selection")
        if selection:
            _reject_control_characters(selection)
            return cmd.set(args.get("setting", ""), args.get("value", ""), selection)
        return cmd.set(args.get("setting", ""), args.get("value", ""))

    def _cartoon(args):
        return cmd.cartoon(args.get("type", "automatic"), args.get("selection", "all"))

    def _spectrum(args):
        return cmd.spectrum(
            args.get("expression", "count"),
            args.get("palette", "rainbow"),
            args.get("selection", "all"),
        )

    def _label(args):
        expression = args.get("expression", "name")
        check_atom_expression(expression)
        return cmd.label(args.get("selection", "all"), expression)

    def _distance(args):
        return cmd.distance(
            args.get("name", "dist"),
            args.get("selection1", "(pk1)"),
            args.get("selection2", "(pk2)"),
        )

    def _angle(args):
        return cmd.angle(
            args.get("name", "angle"),
            args.get("selection1", "(pk1)"),
            args.get("selection2", "(pk2)"),
            args.get("selection3", "(pk3)"),
        )

    def _dihedral(args):
        return cmd.dihedral(
            args.get("name", "dihedral"),
            args.get("selection1", "(pk1)"),
            args.get("selection2", "(pk2)"),
            args.get("selection3", "(pk3)"),
            args.get("selection4", "(pk4)"),
        )

    def _center(args):
        return cmd.center(args.get("selection", "all"))

    def _orient(args):
        return cmd.orient(args.get("selection", "all"))

    def _zoom(args):
        buffer_val = args.get("buffer", "5")
        try:
            buffer_val = float(buffer_val)
        except (ValueError, TypeError):
            buffer_val = 5.0
        return cmd.zoom(args.get("selection", "all"), buffer_val)

    def _reset(args):
        obj = args.get("object")
        if obj:
            return cmd.reset(obj)
        return cmd.reset()

    def _turn(args):
        angle = args.get("angle", "90")
        try:
            angle = float(angle)
        except (ValueError, TypeError):
            angle = 90.0
        return cmd.turn(args.get("axis", "y"), angle)

    def _move(args):
        distance = args.get("distance", "1")
        try:
            distance = float(distance)
        except (ValueError, TypeError):
            distance = 1.0
        return cmd.move(args.get("axis", "z"), distance)

    def _clip(args):
        distance = args.get("distance", "1")
        try:
            distance = float(distance)
        except (ValueError, TypeError):
            distance = 1.0
        return cmd.clip(args.get("mode", "near"), distance)

    def _load(args):
        filename = args.get("filename", "")
        obj = args.get("object")
        if obj:
            return cmd.load(filename, obj)
        return cmd.load(filename)

    def _fetch(args):
        code = args.get("code", "")
        name = args.get("name")
        if name:
            return cmd.fetch(code, name)
        return cmd.fetch(code)

    def _save_selection(selection):
        r"""Normalise a save selection, defaulting the way cmd.save does.

        cmd.save's own default is `(all)`, parenthesised, and the parentheses
        are load-bearing: a bare word is read as an object name, and since no
        object is called "all" it matches nothing. For a .pse that produces a
        ~1 kB settings-only session file, saved and reported as a success --

            cmd.save(p, "all",   -1)  ->   1011 bytes, no objects
            cmd.save(p, "(all)", -1)  ->  10506 bytes, both objects

        which is the "saved successfully but contains nothing" failure the
        skill documented without ever explaining. Coordinate formats are
        unaffected (a .pdb comes out identical either way), so it only ever
        bit session files.
        """
        if not selection or selection == "all":
            return "(all)"
        return selection

    def _save(args):
        state = args.get("state", "-1")
        try:
            state = int(state)
        except (ValueError, TypeError):
            state = -1
        return cmd.save(
            args.get("filename", ""),
            _save_selection(args.get("selection")),
            state,
        )

    def _png(args):
        direct = {
            name: args[name]
            for name in ("width", "height", "dpi", "ray", "quiet")
            if name in args
        }
        if direct and args.get("options"):
            raise ValueError("use either typed PNG arguments or options, not both")
        options = (
            _parse_png_options(
                ", ".join("%s=%s" % (name, value) for name, value in direct.items())
            )
            if direct
            else _parse_png_options(args.get("options", ""))
        )
        return cmd.png(args.get("filename", "output.png"), **options)

    def _get_view(args):
        return json.dumps([float(item) for item in cmd.get_view()])

    def _set_view(args):
        return cmd.set_view(_parse_view(args.get("view")))

    def _get_setting(args):
        name = _check_setting_name(args.get("name", ""))
        return json.dumps({"name": name, "value": cmd.get(name)})

    def _select(args):
        return cmd.select(args.get("name", "sele"), args.get("selection", "all"))

    def _deselect(args):
        return cmd.deselect()

    def _create(args):
        source_state = args.get("source_state", "1")
        try:
            source_state = int(source_state)
        except (ValueError, TypeError):
            source_state = 1
        return cmd.create(
            args.get("name", "obj"), args.get("selection", "all"), source_state
        )

    def _extract(args):
        return cmd.extract(args.get("name", "obj"), args.get("selection", "all"))

    def _delete(args):
        return cmd.delete(args.get("name", "all"))

    def _remove(args):
        return cmd.remove(args.get("selection", "none"))

    def _align(args):
        return cmd.align(args.get("mobile", "all"), args.get("target", "all"))

    def _super(args):
        return cmd.super(args.get("mobile", "all"), args.get("target", "all"))

    def _intra_fit(args):
        return cmd.intra_fit(args.get("selection", "all"))

    def _intra_rms(args):
        return cmd.intra_rms(args.get("selection", "all"))

    def _alter(args):
        expression = args.get("expression", "")
        check_atom_expression(expression)
        return cmd.alter(args.get("selection", "all"), expression)

    def _alter_state(args):
        expression = args.get("expression", "")
        check_atom_expression(expression, coordinates=True)
        state = args.get("state", "1")
        try:
            state = int(state)
        except (ValueError, TypeError):
            state = 1
        return cmd.alter_state(state, args.get("selection", "all"), expression)

    def _h_add(args):
        return cmd.h_add(args.get("selection", "all"))

    def _h_fill(args):
        return cmd.h_fill(args.get("selection", "all"))

    def _bond(args):
        order = args.get("order", "1")
        try:
            order = int(order)
        except (ValueError, TypeError):
            order = 1
        return cmd.bond(args.get("atom1", ""), args.get("atom2", ""), order)

    def _unbond(args):
        return cmd.unbond(args.get("atom1", ""), args.get("atom2", ""))

    def _rebuild(args):
        return cmd.rebuild(args.get("selection", "all"))

    def _refresh(args):
        return cmd.refresh()

    def _spheroid(args):
        # cmd.spheroid takes an object name directly; going through cmd.do
        # would put PyMOL's command interpreter back in the path.
        selection = args.get("selection", "all")
        _reject_control_characters(selection)
        return cmd.spheroid(selection)

    def _isomesh(args):
        level = args.get("level", "1.0")
        try:
            level = float(level)
        except (ValueError, TypeError):
            level = 1.0
        return cmd.isomesh(
            args.get("name", "mesh"),
            args.get("map_object", ""),
            level,
            args.get("selection", "all"),
        )

    def _isosurface(args):
        level = args.get("level", "1.0")
        try:
            level = float(level)
        except (ValueError, TypeError):
            level = 1.0
        return cmd.isosurface(
            args.get("name", "surf"),
            args.get("map_object", ""),
            level,
            args.get("selection", "all"),
        )

    def _sculpt_activate(args):
        return cmd.sculpt_activate(args.get("object", "all"))

    def _sculpt_deactivate(args):
        return cmd.sculpt_deactivate(args.get("object", "all"))

    def _sculpt_iterate(args):
        iterations = args.get("iterations", "1")
        try:
            iterations = int(iterations)
        except (ValueError, TypeError):
            iterations = 1
        return cmd.sculpt_iterate(args.get("object", "all"), iterations)

    def _scene(args):
        return cmd.scene(args.get("key", ""), args.get("action", "recall"))

    def _scene_order(args):
        return cmd.scene_order(args.get("scene_list", ""))

    def _mset(args):
        return cmd.mset(args.get("specification", "1"))

    def _mplay(args):
        return cmd.mplay()

    def _mstop(args):
        return cmd.mstop()

    def _frame(args):
        frame = args.get("frame_number")
        if frame:
            try:
                frame = int(frame)
            except (ValueError, TypeError):
                frame = 1
            return cmd.frame(frame)
        return cmd.frame()

    def _forward(args):
        return cmd.forward()

    def _backward(args):
        return cmd.backward()

    def _rock(args):
        return cmd.rock()

    def _ray(args):
        width = args.get("width")
        height = args.get("height")
        w = 0
        h = 0
        if width:
            try:
                w = int(width)
            except (ValueError, TypeError):
                pass
        if height:
            try:
                h = int(height)
            except (ValueError, TypeError):
                pass
        return cmd.ray(w, h)

    def _draw(args):
        width = args.get("width")
        height = args.get("height")
        w = 0
        h = 0
        if width:
            try:
                w = int(width)
            except (ValueError, TypeError):
                pass
        if height:
            try:
                h = int(height)
            except (ValueError, TypeError):
                pass
        return cmd.draw(w, h)

    def _mpng(args):
        return cmd.mpng(args.get("prefix", "frame"))

    def _symexp(args):
        cutoff = args.get("cutoff", "20")
        try:
            cutoff = float(cutoff)
        except (ValueError, TypeError):
            cutoff = 20.0
        return cmd.symexp(
            args.get("prefix", "sym"), args.get("selection", "all"), cutoff
        )

    def _set_symmetry(args):
        vals = []
        for key in ["a", "b", "c", "alpha", "beta", "gamma"]:
            v = args.get(key, "0")
            try:
                vals.append(float(v))
            except (ValueError, TypeError):
                vals.append(0.0)
        return cmd.set_symmetry(args.get("selection", "all"), *vals)

    def _fab(args):
        return cmd.fab(args.get("sequence", "ALA"), args.get("options", ""))

    def _fragment(args):
        return cmd.fragment(args.get("name", ""))

    def _full_screen(args):
        return cmd.full_screen()

    def _viewport(args):
        width = args.get("width", "640")
        height = args.get("height", "480")
        try:
            width = int(width)
            height = int(height)
        except (ValueError, TypeError):
            width, height = 640, 480
        return cmd.viewport(width, height)

    def _help(args):
        command = args.get("command")
        if command:
            return cmd.help(command)
        return cmd.help()

    ##########################################################################
    # INTROSPECTION
    #
    # These return structured data rather than acting on the view, and exist
    # because the alternative is asking a caller to infer facts from side
    # effects: counting atoms by reading a `select` reply, or discovering chain
    # IDs from what `util.cbc` happens to print while recolouring the object.
    #
    # None of them evaluates a caller-supplied expression. They read fixed atom
    # properties into plain Python and return it, which is what keeps them
    # outside check_atom_expression's remit and preserves the no-exec property
    # of the dispatcher.
    ##########################################################################

    DNA_RESN = {"DA", "DC", "DG", "DT", "DI"}

    def _residue_index(selection):
        """Map (chain, resi) -> resn for every residue in a selection.

        The expression must be an assignment, not `seen.setdefault(...)`.
        cmd.iterate echoes the value of each evaluated expression, so a call
        that returns something prints once per atom -- tens of thousands of
        lines that then swamp the captured output the handler's real result
        travels in. Assignment evaluates to nothing and stays silent.
        """
        seen = {}
        cmd.iterate(
            selection,
            "seen[(chain, int(resv))] = resn",
            space={"seen": seen, "int": int},
        )
        return seen

    def _classify(resn_set, has_ca):
        if not resn_set:
            return "empty"
        nucleic = {"A", "C", "G", "U"} | DNA_RESN
        if resn_set <= DNA_RESN:
            return "dna"
        if resn_set <= nucleic:
            return "rna" if not (resn_set & DNA_RESN) else "mixed"
        if resn_set == {"HOH"}:
            return "solvent"
        if has_ca:
            return "protein"
        if len(resn_set) <= 2:
            return "ligand"
        return "mixed"

    def _gaps_from(numbers):
        ordered = sorted(numbers)
        return [
            [a + 1, b - 1]
            for a, b in zip(ordered, ordered[1:])
            if b > a + 1
        ]

    def _get_chains(args):
        obj = args.get("object") or "all"
        out = []
        for chain in cmd.get_chains(obj):
            sel = f"({obj}) and chain {chain}"
            index = _residue_index(sel)
            numbers = [resi for (_, resi) in index]
            resns = set(index.values())
            has_ca = cmd.count_atoms(f"{sel} and name CA") > 0
            out.append(
                {
                    "chain": chain,
                    "kind": _classify(resns, has_ca),
                    "atoms": cmd.count_atoms(sel),
                    "residues": len(index),
                    "first": min(numbers) if numbers else None,
                    "last": max(numbers) if numbers else None,
                    "gaps": _gaps_from(numbers),
                }
            )
        return {"object": obj, "chains": out}

    def _count(args):
        sel = args.get("selection") or "all"
        index = _residue_index(sel)
        return {
            "selection": sel,
            "atoms": cmd.count_atoms(sel),
            "residues": len(index),
            "chains": len({chain for (chain, _) in index}),
        }

    def _list_residues(args):
        sel = args.get("selection") or "all"
        limit = int(args.get("limit", 5000))
        index = _residue_index(sel)
        ordered = sorted(index.items(), key=lambda kv: (kv[0][0], kv[0][1]))
        truncated = len(ordered) > limit
        return {
            "selection": sel,
            "residues": [
                {"chain": chain, "resi": resi, "resn": resn}
                for (chain, resi), resn in ordered[:limit]
            ],
            "truncated": truncated,
        }

    def _contacts(args):
        """Residues of `selection` near `near`.

        Narrowing happens in `selection` -- restrict it to named atoms to ask
        "residues whose CA is in range" rather than "residues with any atom in
        range". Writing that by hand is where `byres` placement silently
        changes the meaning.
        """
        sel = args.get("selection")
        near = args.get("near")
        if not sel or not near:
            raise ValueError("contacts requires both 'selection' and 'near'")
        try:
            within = float(args.get("within", 4.0))
        except (TypeError, ValueError):
            raise ValueError("'within' must be a number")
        if not 0 < within <= 50:
            raise ValueError("'within' must be between 0 and 50 angstroms")
        # Always expand to whole residues: the return is a residue list, so
        # collapsing atoms to residues erases any difference a non-expanded
        # shell would have made. Callers narrow the question through the
        # selection's atom names instead.
        target = f"byres (({sel}) within {within} of ({near}))"
        index = _residue_index(target)
        ordered = sorted(index.items(), key=lambda kv: (kv[0][0], kv[0][1]))
        return {
            "selection": target,
            "residues": [
                {"chain": chain, "resi": resi, "resn": resn}
                for (chain, resi), resn in ordered
            ],
            "truncated": False,
        }

    def _get_gaps(args):
        obj = args.get("object") or "all"
        chain = args.get("chain")
        sel = f"({obj}) and chain {chain}" if chain else f"({obj})"
        index = _residue_index(sel)
        numbers = sorted(resi for (_, resi) in index)
        return {
            "object": obj,
            "chain": chain or "",
            "first": numbers[0] if numbers else None,
            "last": numbers[-1] if numbers else None,
            "modelled": len(numbers),
            "gaps": _gaps_from(numbers),
        }

    AA3TO1 = {
        "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
        "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
        "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
        "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    }

    def _ss_of(selection):
        """(chain, resi) -> H | S | L for every residue.

        PyMOL stores loop as an empty ss, not "L", which is why `ss L` in a
        selection silently matches nothing. Normalising here means callers
        never meet that.
        """
        raw = {}
        cmd.iterate(
            selection,
            "raw[(chain, int(resv))] = ss",
            space={"raw": raw, "int": int},
        )
        return {k: (v if v in ("H", "S") else "L") for k, v in raw.items()}

    def _get_secondary_structure(args):
        sel = args.get("selection") or "all"
        table = _ss_of("(%s) and name CA" % sel)
        ordered = sorted(table.items(), key=lambda kv: (kv[0][0], kv[0][1]))
        residues = [
            {"chain": chain, "resi": resi, "ss": ss}
            for (chain, resi), ss in ordered
        ]
        # Runs are what people actually read: "22 H, 3 L, 15 H" says
        # helix-turn-helix, where 37 H and 3 L does not.
        runs = []
        for entry in residues:
            if runs and runs[-1]["ss"] == entry["ss"] and \
                    runs[-1]["chain"] == entry["chain"] and \
                    runs[-1]["end"] + 1 == entry["resi"]:
                runs[-1]["end"] = entry["resi"]
                runs[-1]["length"] += 1
            else:
                runs.append({
                    "chain": entry["chain"], "ss": entry["ss"],
                    "start": entry["resi"], "end": entry["resi"], "length": 1,
                })
        counts = {"H": 0, "S": 0, "L": 0}
        for entry in residues:
            counts[entry["ss"]] += 1
        return {
            "selection": sel,
            "residues": residues,
            "runs": runs,
            "helix": counts["H"],
            "sheet": counts["S"],
            "loop": counts["L"],
            "pattern": "".join(
                "%d%s" % (r["length"], r["ss"]) for r in runs
            ),
        }

    def _get_sequence(args):
        sel = args.get("selection") or "all"
        table = {}
        cmd.iterate(
            "(%s) and name CA+C1'" % sel,
            "table[(chain, int(resv))] = resn",
            space={"table": table, "int": int},
        )
        ordered = sorted(table.items(), key=lambda kv: (kv[0][0], kv[0][1]))
        chains = {}
        for (chain, resi), resn in ordered:
            entry = chains.setdefault(
                chain, {"chain": chain, "first": resi, "last": resi, "seq": ""}
            )
            # Nucleotides are already one or two characters; strip the deoxy D.
            letter = AA3TO1.get(resn, resn[-1] if len(resn) <= 2 else "X")
            entry["seq"] += letter
            entry["last"] = resi
        return {"selection": sel, "chains": list(chains.values())}

    def _measure(args):
        """Distance between two selections, with no scene side effect.

        cmd.distance would answer the same question but leaves a labelled
        distance object behind that then appears in every render. Reading a
        number should not change the picture.
        """
        a = args.get("selection1")
        b = args.get("selection2")
        if not a or not b:
            raise ValueError("measure requires 'selection1' and 'selection2'")
        for name, sel in (("selection1", a), ("selection2", b)):
            n = cmd.count_atoms(sel)
            if n != 1:
                raise ValueError(
                    "%s must match exactly one atom, matched %d: %s"
                    % (name, n, sel)
                )
        return {
            "selection1": a,
            "selection2": b,
            "distance": round(cmd.get_distance(a, b), 4),
        }

    def _clear_selections(args):
        """Delete every named selection.

        Named selections draw as magenta dots in a ray trace, so they have to
        go before rendering. Doing it one delete at a time means knowing every
        name you created.
        """
        names = list(cmd.get_names("selections"))
        for name in names:
            cmd.delete(name)
        cmd.deselect()
        return {"deleted": names, "count": len(names)}

    def _inspect_setting(args):
        """Read a setting at every layer it can live on.

        cmd.get -- what the plain `get_setting` command calls -- reads the
        global layer and returns it formatted as a string ('0.60000'). That
        makes it useless for the failure it gets reached for: a selection
        scoped `set` writes per-atom values that outlive hide, show, recolour
        and any later global set, and the global reads clean the whole time.

        The setting name is passed through `space` and read with getattr. It is
        never interpolated into the expression, which is what keeps this out of
        check_atom_expression's remit and off the list of things that can
        smuggle in code.
        """
        name = _check_setting_name(args.get("name", ""))
        sel = args.get("selection") or "all"
        _reject_control_characters(sel)

        rows = []
        cmd.iterate(
            sel,
            "rows.append((model, getattr(s, _name)))",
            space={"rows": rows, "getattr": getattr, "_name": name},
        )

        def plain(value):
            """Normalise a setting value for comparison and for JSON.

            PyMOL stores settings as C floats, and the two readers disagree
            about the width: the per-atom read through `s.` widens to a double
            (0.6 comes back as 0.6000000238418579) while get_setting_tuple
            returns 0.6. Comparing those raw makes every setting look
            overridden. Six significant figures is more than a float32 carries,
            so this loses nothing real and keeps small values intact.
            """
            if isinstance(value, (tuple, list)):
                return [plain(item) for item in value]
            if isinstance(value, float):
                return float("%.6g" % value)
            return value

        groups = {}
        objects = set()
        for model, value in rows:
            objects.add(model)
            normalised = plain(value)
            entry = groups.setdefault(
                repr(normalised),
                {"value": normalised, "atoms": 0, "objects": set()},
            )
            entry["atoms"] += 1
            entry["objects"].add(model)

        ordered = sorted(groups.values(), key=lambda g: -g["atoms"])
        truncated = len(ordered) > 20
        values = [
            {
                "value": g["value"],
                "atoms": g["atoms"],
                "objects": sorted(g["objects"]),
            }
            for g in ordered[:20]
        ]

        global_value = plain(cmd.get_setting_tuple(name)[1][0])
        object_values = []
        for model in sorted(objects):
            try:
                object_values.append(
                    {
                        "object": model,
                        "value": plain(cmd.get_setting_tuple(name, model)[1][0]),
                    }
                )
            except Exception:
                continue

        base = {entry["object"]: entry["value"] for entry in object_values}
        overridden = any(
            g["value"] != base.get(model, global_value)
            for g in ordered
            for model in g["objects"]
        )

        return {
            "name": name,
            "selection": sel,
            "atoms": len(rows),
            "display": str(cmd.get(name)),
            "global_value": global_value,
            "object_values": object_values,
            "values": values,
            "uniform": len(ordered) <= 1,
            "overridden": overridden,
            "truncated": truncated,
        }

    def _unset(args):
        """Clear a setting override at a chosen layer.

        `scope` exists because in PyMOL the layer is chosen by punctuation: a
        bare identifier writes the object layer, anything parenthesised or
        compound writes the atoms, and clearing the wrong one reports success
        while changing nothing. See _atom_scope for the measurements.

        When `scope` is absent the selection passes through untouched. That is
        the string path -- `parse_and_execute("unset x, ala")` has to behave as
        native PyMOL does, or the command table stops being a faithful mirror
        of PyMOL syntax. The typed tool always sends a scope.
        """
        name = _check_setting_name(args.get("setting", ""))
        scope = args.get("scope")
        selection = args.get("selection")

        if scope == "global" or (scope is None and not selection):
            return cmd.unset(name)
        if not selection:
            raise ValueError("unset with scope=%r needs a selection" % scope)
        _reject_control_characters(selection)
        if scope == "atom":
            return cmd.unset(name, _atom_scope(selection))
        return cmd.unset(name, selection)

    def _get_representations(args):
        """What is currently shown, per object and chain.

        `hide everything` has no undo and destroys the representation state for
        its selection, so the advice used to be that you cannot know what was
        shown before -- you were choosing a representation when you restored,
        not recovering one. The per-atom `reps` bitmask has been readable the
        whole time; this exposes it.

        Aggregated to (object, chain, mask) rather than returned per atom. The
        payload is then bounded by the number of distinct groups, not by the
        size of the structure.
        """
        sel = args.get("selection") or "all"
        _reject_control_characters(sel)

        acc = {}
        cmd.iterate(
            sel,
            "acc[(model, chain, reps)] = acc.get((model, chain, reps), 0) + 1",
            space={"acc": acc},
        )

        totals = {}
        for (model, chain, _mask), count in acc.items():
            totals[(model, chain)] = totals.get((model, chain), 0) + count

        groups = []
        for (model, chain), atoms in sorted(totals.items()):
            per_rep = {}
            for (m, c, mask), count in acc.items():
                if (m, c) != (model, chain):
                    continue
                for name, bit in ATOM_LEVEL_REPS.items():
                    if mask & (1 << bit):
                        per_rep[name] = per_rep.get(name, 0) + count
            groups.append(
                {
                    "object": model,
                    "chain": chain,
                    "atoms": atoms,
                    "reps": sorted(per_rep),
                    "per_rep": [
                        {"rep": name, "atoms": per_rep[name]}
                        for name in sorted(per_rep)
                    ],
                    # A rep on some but not all of a group looks identical to a
                    # rep on all of it in a render, and means something quite
                    # different.
                    "partial": any(count < atoms for count in per_rep.values()),
                }
            )

        union = sorted({name for group in groups for name in group["reps"]})
        total_atoms = sum(group["atoms"] for group in groups)
        return {
            "selection": sel,
            "atoms": total_atoms,
            "reps": union,
            "groups": groups,
            "hidden": total_atoms > 0 and not union,
            "note": (
                "Object-level representations (%s) never appear in a per-atom "
                "mask, so this cannot report them either way."
                % ", ".join(sorted(OBJECT_LEVEL_REPS))
            ),
        }

    def _get_history(args):
        """Read back the session history the plugin has been writing.

        The alternative was telling the caller to shell out and grep
        history.jsonl, which contradicts the rest of the protocol and only
        works for a caller that can reach the filesystem PyMOL is running on.

        Read here rather than server-side: the history directory comes from
        PYMOL_MCP_HISTORY in the environment PyMOL was launched from, which is
        not the server's environment. Only this process knows where it is
        writing, and only this process knows this session's replay script.
        """
        directory = _history_directory()
        if directory is None:
            return {
                "enabled": False,
                "directory": None,
                "script": None,
                "entries": [],
                "total": 0,
                "truncated": False,
            }

        try:
            limit = int(args.get("limit", 20))
        except (ValueError, TypeError):
            limit = 20
        limit = max(1, min(limit, 500))
        want = args.get("command")
        failed_only = bool(args.get("failed_only"))

        # Bounded while streaming: a long-running session's history is not
        # something to hold in memory to then throw away all but the tail of.
        kept = deque(maxlen=limit)
        total = 0
        path = os.path.join(directory, "history.jsonl")
        try:
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        # A half-written final record is exactly what a crash
                        # leaves behind, and recovery is when this is read.
                        continue
                    if not isinstance(record, dict):
                        continue
                    if failed_only and record.get("ok", True):
                        continue
                    if want and record.get("command") != want:
                        continue
                    total += 1
                    kept.append(record)
        except OSError:
            pass

        script = _history_pml
        if script is None:
            try:
                sessions = sorted(
                    name
                    for name in os.listdir(directory)
                    if name.startswith("session-") and name.endswith(".pml")
                )
                if sessions:
                    script = os.path.join(directory, sessions[-1])
            except OSError:
                script = None

        return {
            "enabled": True,
            "directory": directory,
            "script": script,
            "entries": list(kept),
            "total": total,
            "truncated": total > len(kept),
        }

    def _export_session(args):
        """Write one session's audit, replay and final-state evidence to ZIP."""
        directory = _history_directory()
        if directory is None:
            raise ValueError("session history is disabled")

        filename = args.get("filename", "")
        if not filename:
            raise ValueError("export_session requires a .zip filename")
        _reject_control_characters(filename)
        target = os.path.abspath(os.path.expanduser(str(filename)))
        if not target.lower().endswith(".zip"):
            raise ValueError("export_session requires a .zip filename")

        current_script = _history_pml
        current_id = None
        if current_script:
            base = os.path.basename(current_script)
            current_id = base[len("session-") : -len(".pml")]
        session_id = args.get("session_id") or current_id
        if not session_id:
            raise ValueError("this PyMOL has no recorded session to export")
        session_id = str(session_id)
        if (
            not session_id
            or ".." in session_id
            or not all(ch.isalnum() or ch in "._-" for ch in session_id)
        ):
            raise ValueError("invalid session_id")

        records = []
        history_path = os.path.join(directory, "history.jsonl")
        try:
            with open(history_path) as fh:
                for line in fh:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if (
                        isinstance(record, dict)
                        and record.get("session_id") == session_id
                    ):
                        records.append(record)
        except OSError:
            pass
        if not records:
            raise ValueError("no history records found for session_id %s" % session_id)

        replay_path = os.path.join(directory, "session-%s.pml" % session_id)
        try:
            with open(replay_path) as fh:
                replay_text = fh.read()
        except OSError:
            replay_text = ""

        artifacts = [
            {
                "ts": record.get("ts"),
                "command": record.get("command"),
                "direction": record["file"].get("direction"),
                "path": record["file"].get("path"),
            }
            for record in records
            if isinstance(record.get("file"), dict)
        ]

        redacted = bool(args.get("redact_paths", False))
        replacements = {}
        if redacted:
            paths = {
                str(item["path"])
                for item in artifacts
                if item.get("path")
            }
            paths.update(
                path
                for path in (directory, os.getcwd(), os.path.expanduser("~"))
                if path
            )
            for index, path in enumerate(sorted(paths, key=len, reverse=True), 1):
                replacements[path] = "<PATH_%03d>" % index

        def redact(value):
            if isinstance(value, dict):
                return {key: redact(item) for key, item in value.items()}
            if isinstance(value, list):
                return [redact(item) for item in value]
            if isinstance(value, str):
                for original, replacement in replacements.items():
                    value = value.replace(original, replacement)
            return value

        current_scene = current_id == session_id
        final_state = {
            "available": current_scene,
            "reason": None if current_scene else "historical session is not live",
        }
        if current_scene:
            enabled = set(cmd.get_names("objects", enabled_only=1))
            objects = []
            for name in sorted(cmd.get_object_list("all")):
                try:
                    object_type = cmd.get_type(name)
                except Exception:
                    object_type = None
                objects.append(
                    {
                        "name": name,
                        "type": object_type,
                        "atoms": int(cmd.count_atoms(name)),
                        "states": int(cmd.count_states(name)),
                        "enabled": name in enabled,
                    }
                )
            try:
                representations = _get_representations({"selection": "all"})
            except Exception:
                representations = None
            final_state.update(
                {
                    "objects": objects,
                    "selections": sorted(cmd.get_names("selections")),
                    "view": [float(item) for item in cmd.get_view()],
                    "representations": representations,
                }
            )

        try:
            version = cmd.get_version()
        except Exception:
            version = None
        replay_commands = sum(
            len(record["replay"])
            if isinstance(record.get("replay"), list)
            else int(bool(record.get("replay")))
            for record in records
            if record.get("ok", True)
        )
        payloads = {
            "history.jsonl": "".join(
                json.dumps(redact(record), sort_keys=True) + "\n"
                for record in records
            ),
            "replay.pml": redact(replay_text),
            "final-state.json": json.dumps(
                redact(final_state), indent=2, sort_keys=True, default=str
            )
            + "\n",
            "artifacts.json": json.dumps(
                redact(artifacts), indent=2, sort_keys=True, default=str
            )
            + "\n",
        }
        manifest = {
            "schema_version": 1,
            "session_id": session_id,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "entries": len(records),
            "failed_entries": sum(not record.get("ok", True) for record in records),
            "replay_commands": replay_commands,
            "replay_available": bool(replay_text),
            "replay_redacted": redacted,
            "redacted_paths": redacted,
            "current_scene": current_scene,
            "artifacts": len(artifacts),
            "pymol": {
                "pid": os.getpid(),
                "port": current_port,
                "version": version,
            },
            "files": sorted(["manifest.json"] + list(payloads)),
        }
        payloads["manifest.json"] = json.dumps(
            manifest, indent=2, sort_keys=True, default=str
        ) + "\n"

        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".pymol-session-", suffix=".zip", dir=parent or None
        )
        os.close(descriptor)
        try:
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for name in sorted(payloads):
                    archive.writestr(name, payloads[name])
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass

        digest = hashlib.sha256()
        with open(target, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return {
            "path": target,
            "bytes": os.path.getsize(target),
            "sha256": digest.hexdigest(),
            "session_id": session_id,
            "entries": len(records),
            "replay_commands": replay_commands,
            "artifacts": len(artifacts),
            "redacted_paths": redacted,
            "current_scene": current_scene,
            "files": manifest["files"],
        }

    def _save_file(args):
        """Save, and report what was actually written.

        cmd.save returns None whatever happens, so the plain `save` command can
        only ever answer "executed successfully" -- which is why verifying a
        .pse meant saving it, reopening it in a fresh PyMOL and counting the
        objects by hand. The facts are all available here; returning them turns
        that ritual into a return value.

        The path is resolved here rather than server-side: cmd.save resolves a
        relative filename against PyMOL's working directory, which the server
        cannot see.
        """
        filename = args.get("filename", "")
        if not filename:
            raise ValueError("save_file requires a filename")
        _reject_control_characters(filename)
        sel = _save_selection(args.get("selection"))
        _reject_control_characters(sel)
        try:
            state = int(args.get("state", -1))
        except (ValueError, TypeError):
            state = -1

        cmd.save(filename, sel, state)

        path = os.path.abspath(os.path.expanduser(filename))
        objects = list(cmd.get_object_list(sel))
        result = {
            "path": path,
            "bytes": os.path.getsize(path),
            "format": os.path.splitext(path)[1].lstrip(".").lower(),
            "selection": sel,
            "objects": objects,
            "object_count": len(objects),
            "atoms": cmd.count_atoms(sel),
            "states": cmd.count_states(sel),
            "objects_verified": [],
        }

        # A .pse is a pickle, and a settings-only one saves and reports success
        # exactly like a full session. Checking that each object's name appears
        # in the bytes is necessary rather than sufficient, but it catches that
        # failure without unpickling anything. Names shorter than three
        # characters are skipped -- too likely to occur by chance.
        if result["format"] == "pse":
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
                result["objects_verified"] = [
                    name
                    for name in objects
                    if len(name) >= 3 and name.encode("utf-8", "replace") in data
                ]
            except OSError:
                pass

        return result

    def _enable(args):
        return cmd.enable(args.get("name", "all"))

    def _disable(args):
        return cmd.disable(args.get("name", "all"))

    # Build the dispatcher — only these commands are allowed
    dispatcher = {
        "get_chains": _get_chains,
        "get_secondary_structure": _get_secondary_structure,
        "get_sequence": _get_sequence,
        "measure": _measure,
        "clear_selections": _clear_selections,
        "count": _count,
        "list_residues": _list_residues,
        "contacts": _contacts,
        "get_gaps": _get_gaps,
        "save_file": _save_file,
        "get_history": _get_history,
        "export_session": _export_session,
        "get_representations": _get_representations,
        "inspect_setting": _inspect_setting,
        "unset": _unset,
        "enable": _enable,
        "disable": _disable,
        "show": _show,
        "hide": _hide,
        "color": _color,
        "bg_color": _bg_color,
        "as": _as,
        "set": _set,
        "cartoon": _cartoon,
        "spectrum": _spectrum,
        "label": _label,
        "distance": _distance,
        "angle": _angle,
        "dihedral": _dihedral,
        "center": _center,
        "orient": _orient,
        "zoom": _zoom,
        "reset": _reset,
        "turn": _turn,
        "move": _move,
        "clip": _clip,
        "load": _load,
        "fetch": _fetch,
        "save": _save,
        "png": _png,
        "get_view": _get_view,
        "set_view": _set_view,
        "get_setting": _get_setting,
        "select": _select,
        "deselect": _deselect,
        "create": _create,
        "extract": _extract,
        "delete": _delete,
        "remove": _remove,
        "align": _align,
        "super": _super,
        "intra_fit": _intra_fit,
        "intra_rms": _intra_rms,
        "alter": _alter,
        "alter_state": _alter_state,
        "h_add": _h_add,
        "h_fill": _h_fill,
        "bond": _bond,
        "unbond": _unbond,
        "rebuild": _rebuild,
        "refresh": _refresh,
        "spheroid": _spheroid,
        "isomesh": _isomesh,
        "isosurface": _isosurface,
        "sculpt_activate": _sculpt_activate,
        "sculpt_deactivate": _sculpt_deactivate,
        "sculpt_iterate": _sculpt_iterate,
        "scene": _scene,
        "scene_order": _scene_order,
        "mset": _mset,
        "mplay": _mplay,
        "mstop": _mstop,
        "frame": _frame,
        "forward": _forward,
        "backward": _backward,
        "rock": _rock,
        "ray": _ray,
        "draw": _draw,
        "mpng": _mpng,
        "symexp": _symexp,
        "set_symmetry": _set_symmetry,
        "fab": _fab,
        "fragment": _fragment,
        "full_screen": _full_screen,
        "viewport": _viewport,
        "help": _help,
    }

    # util.* commands are called directly; see _util_command for why.
    util_commands = [
        "util.cbc",
        "util.cbaw",
        "util.cbag",
        "util.cbac",
        "util.cbam",
        "util.cbay",
        "util.cbas",
        "util.cbab",
        "util.cbao",
        "util.cbap",
        "util.cbak",
        "util.chainbow",
        "util.rainbow",
    ]
    for util_cmd in util_commands:
        dispatcher[util_cmd] = lambda args, name=util_cmd: _util_command(name, args)

    return dispatcher


##############################################################################
# SOCKET SERVER
##############################################################################


class SocketServer:
    def __init__(self, host="localhost", port=9876):
        self.host = host
        self.port = port
        self.socket = None
        self.running = False
        self.thread = None
        self.command_callback = None
        # Connections are served concurrently, one thread each.
        self._clients = set()
        self._clients_lock = threading.Lock()
        # ...but PyMOL itself is driven one command at a time.
        self._command_lock = threading.Lock()
        # Set by stop() so the accept loop can tell a requested shutdown from
        # a crash, and only report the latter.
        self._stopping = False

    def start(self, command_callback=None):
        """
        Start the socket server on a separate thread.

        Binds before returning, so a True result means the port really is
        listening. Binding in the worker thread instead would make this return
        True even when the port is already in use -- the caller would report a
        listener that does not exist -- and would let a client connect before
        the socket was ready.
        """
        if self.running:
            return False

        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                # Windows only. There SO_REUSEADDR means what SO_REUSEPORT means
                # on Unix: a second socket may bind a port that is already bound,
                # and connections go to an arbitrary one. That would let two
                # PyMOL instances both claim a port and break both the "bind
                # before reporting success" guarantee and port auto-allocation.
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                # POSIX: only permits rebinding a port left in TIME_WAIT. It does
                # not allow two live listeners, so the bind below still fails.
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(8)
            self.socket.settimeout(1.0)
        except OSError as e:
            _log(f"Could not listen on {self.host}:{self.port}: {e}")
            if self.socket:
                self.socket.close()
                self.socket = None
            return False

        self.command_callback = command_callback
        self._stopping = False
        self.running = True
        self.thread = threading.Thread(target=self._run_server)
        self.thread.daemon = True
        self.thread.start()
        return True

    def _run_server(self):
        """Run the accept loop; the socket is already bound by start().

        The loop must survive anything a single connection throws. It used to
        wrap the whole `while` in one try, so one escaped exception closed the
        socket and ended the thread for the life of the PyMOL process, with no
        message anywhere. If the loop does exit unexpectedly, that is reported
        unconditionally rather than through _log, because a dead listener is
        the one failure that cannot report itself through the socket.
        """
        failure = None
        try:
            _log(f"PyMOL MCP Socket server listening on {self.host}:{self.port}")

            while self.running:
                try:
                    client, address = self.socket.accept()
                except socket.timeout:
                    continue
                except Exception as e:
                    # Never terminal. Anything a single accept raises is logged
                    # and the loop goes round again; only self.running going
                    # False ends it normally.
                    _log(f"Error accepting connection: {str(e)}")
                    if VERBOSE:
                        traceback.print_exc()
                    # A dead listening socket cannot be accepted from again, so
                    # stop rather than spinning at full speed on the same error.
                    if self.socket is None or self.socket.fileno() < 0:
                        failure = e
                        break
                    time.sleep(0.1)
                    continue

                _log(f"Connected to client: {address}")
                client.settimeout(1.0)
                with self._clients_lock:
                    self._clients.add(client)
                worker = threading.Thread(target=self._serve_client, args=(client,))
                worker.daemon = True
                worker.start()

        except Exception as e:
            failure = e
        finally:
            if self.socket:
                self.socket.close()
            was_stopping = self._stopping
            self.running = False
            _log("Socket server stopped")
            if not was_stopping:
                _report_listener_death(self.port, failure)

    def _serve_client(self, client):
        """Read and answer one client until it disconnects.

        Runs on its own thread. Previously the accept loop did this inline, so
        the server handled exactly one client at a time and an idle persistent
        connection blocked every other caller indefinitely. The MCP server
        holds its connection open between commands, which meant an instance it
        was attached to could not answer an instance_info probe and dropped out
        of discovery entirely.
        """
        buffer = b""
        try:
            while self.running:
                try:
                    data = client.recv(4096)
                    if not data:
                        break

                    buffer += data
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        if not line.strip():
                            continue
                        try:
                            command = json.loads(line.decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError) as e:
                            response = {
                                "status": "error",
                                "message": "Invalid JSON message: %s" % e,
                            }
                            client.sendall(
                                (json.dumps(response) + "\n").encode("utf-8")
                            )
                            continue

                        # PyMOL is not safe to drive from several threads at once,
                        # so connections are concurrent but commands are not.
                        with self._command_lock:
                            result = self._handle_command(command)

                        failed = isinstance(result, dict) and (
                            result.get("executed") is False
                        )
                        if failed:
                            response = {
                                "status": "error",
                                "message": result.get("error", "Unknown error"),
                            }
                        else:
                            response = {
                                "status": "success",
                                "result": result or "Command executed",
                            }
                        client.sendall((json.dumps(response) + "\n").encode("utf-8"))

                except socket.timeout:
                    continue
                except Exception as e:
                    _log(f"Error serving client: {str(e)}")
                    break
        finally:
            with self._clients_lock:
                self._clients.discard(client)
            try:
                client.close()
            except Exception:
                pass

    def _handle_command(self, command):
        """Handle received structured command"""
        if not command:
            return

        cmd_type = command.get("type", "")
        if cmd_type == "instance_info":
            return describe_instance(self.port)
        if cmd_type != "structured_command":
            return {"executed": False, "error": f"Unknown message type: {cmd_type}"}

        global received_commands
        cmd_name = command.get("command", "")
        args = command.get("args", {})
        source = command.get("source")
        replay = command.get("replay")
        received_commands.append(f"{cmd_name} {json.dumps(args)}")

        if self.command_callback and cmd_name:
            result = self.command_callback(cmd_name, args)
            _record_history(cmd_name, args, source, result, replay)
            return result

    def stop(self):
        """Stop the socket server"""
        self._stopping = True
        self.running = False
        if self.thread:
            self.thread.join(3.0)
        with self._clients_lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except Exception:
                pass
        if self.socket:
            self.socket.close()
        self.socket = None
        self.thread = None
