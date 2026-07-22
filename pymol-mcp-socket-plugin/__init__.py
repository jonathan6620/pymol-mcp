'''
PyMOL MCP Plugin

A plugin that listens for socket connections and executes allowlisted PyMOL
commands received as structured JSON. No arbitrary code execution (exec) is used.

Based on the concept of the "Rendering Plugin" from Michael Lerner.
'''

from __future__ import absolute_import, print_function

import ast
import json
import os
import socket
import threading
import time
import traceback
from collections import deque

# Global variables
dialog = None
socket_server = None
# Bounded: nothing reads this back, and an unbounded list would grow for the
# life of the PyMOL process. The on-disk history below is the durable record.
received_commands = deque(maxlen=200)
listening = False
current_port = 9876  # Default port
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
#   session-*.pml   the successful commands only, as literal PyMOL syntax, so
#                   the session can be replayed with `@session-....pml` or
#                   pasted into a methods section
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
    "png": ("filename", "out"),
}

_history_dir = None  # resolved on the first recorded command
_history_pml = None  # this session's replay script
_history_lock = threading.Lock()
_history_broken = False  # set after a write failure; stops retrying


def _history_paths():
    """Resolve and create the history directory. Returns (None, None) if off."""
    global _history_dir, _history_pml

    if _history_dir is not None:
        return _history_dir, _history_pml
    if HISTORY_SETTING.lower() in HISTORY_OFF:
        return None, None

    directory = HISTORY_SETTING or os.path.join(
        os.path.expanduser("~"), ".pymol-mcp"
    )
    os.makedirs(directory, exist_ok=True)

    pml = os.path.join(directory, "session-%s.pml" % time.strftime("%Y%m%d-%H%M%S"))
    with open(pml, "a") as fh:
        fh.write("# pymol-mcp session %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        # Any relative path below was resolved against this directory, so a
        # replay from elsewhere needs it.
        fh.write("# PyMOL working directory: %s\n" % os.getcwd())
        fh.write("# Replay with:  @%s\n\n" % pml)

    _history_dir, _history_pml = directory, pml
    _log("Recording command history to %s" % directory)
    return _history_dir, _history_pml


def _record_history(command_name, args, source, result):
    """Append one command to the history files.

    Never raises. A history write failing must not stop PyMOL executing
    commands, so any error disables recording for the rest of the session
    rather than propagating to the caller.
    """
    global _history_broken

    # No source means an internal call, currently the connection health-check
    # ping, which is not part of the user's session.
    if _history_broken or not source:
        return

    try:
        with _history_lock:
            directory, pml = _history_paths()
            if directory is None:
                return

            ok = not (isinstance(result, dict) and result.get("executed") is False)
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "command": command_name,
                "args": args,
                "source": source,
                "ok": ok,
            }
            if isinstance(result, dict):
                detail = result.get("error") if not ok else result.get("output")
                if detail:
                    # util.cbc and friends can return a lot; keep the file sane.
                    record["error" if not ok else "output"] = str(detail)[:2000]

            spec = FILE_ARGS.get(command_name)
            if spec:
                arg_name, direction = spec
                path = args.get(arg_name)
                if path:
                    record["file"] = {
                        "path": os.path.abspath(os.path.expanduser(str(path))),
                        "direction": direction,
                    }

            with open(os.path.join(directory, "history.jsonl"), "a") as fh:
                fh.write(json.dumps(record) + "\n")

            # A failed command would not replay, so keep the script clean.
            if ok:
                with open(pml, "a") as fh:
                    fh.write(source + "\n")
    except Exception as e:
        _history_broken = True
        _log("Command history disabled after a write error: %s" % e)


def __init_plugin__(app=None):
    '''
    Add an entry to the PyMOL "Plugin" menu
    '''
    from pymol.plugins import addmenuitemqt
    addmenuitemqt('PyMol MCP Socket Plugin', run_plugin_gui)


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
    False if it was already running. Safe to call from `.pymolrc.py`.
    """
    global socket_server, listening, current_port

    if listening:
        return False

    current_port = port or current_port
    socket_server = SocketServer(port=current_port)
    if not socket_server.start(execute_structured_command):
        return False

    listening = True
    return True


def stop_socket_server():
    """Stop the MCP socket listener if it is running."""
    global listening

    if socket_server and listening:
        socket_server.stop()
    listening = False


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
ALTER_NAMES = frozenset({
    "name", "resn", "resi", "resv", "chain", "segi", "elem", "alt", "b", "q",
    "type", "formal_charge", "partial_charge", "numeric_type", "text_type",
    "vdw", "ss", "color", "label", "ID", "index", "rank", "model", "state",
    "cartoon", "flags", "geom", "valence", "protons", "oneletter", "reps",
    "True", "False", "None",
})
ALTER_COORDINATE_NAMES = frozenset({"x", "y", "z"})
# Calls are otherwise blocked outright; these are pure and cannot reach out.
ALTER_CALLS = frozenset({"str", "int", "float", "abs", "round", "len", "min", "max"})

# Anything not listed is rejected, so new syntax fails closed. Attribute and
# Subscript are deliberately absent: they are what make `().__class__` and
# similar sandbox escapes work.
ALTER_NODES = (
    ast.Expression, ast.Constant, ast.Name, ast.Load, ast.Call,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.Tuple, ast.List,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
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

    def _as(args):
        return cmd.show_as(
            args.get("representation", "cartoon"), args.get("selection", "all")
        )

    def _set(args):
        selection = args.get("selection")
        if selection:
            return cmd.set(args.get("setting", ""), args.get("value", ""), selection)
        return cmd.set(args.get("setting", ""), args.get("value", ""))

    def _cartoon(args):
        return cmd.cartoon(args.get("type", "automatic"), args.get("selection", "all"))

    def _spectrum(args):
        return cmd.spectrum(
            args.get("expression", "count"),
            args.get("palette", "rainbow"),
            args.get("selection", "all")
        )

    def _label(args):
        expression = args.get("expression", "name")
        check_atom_expression(expression)
        return cmd.label(args.get("selection", "all"), expression)

    def _distance(args):
        return cmd.distance(
            args.get("name", "dist"),
            args.get("selection1", "(pk1)"),
            args.get("selection2", "(pk2)")
        )

    def _angle(args):
        return cmd.angle(
            args.get("name", "angle"),
            args.get("selection1", "(pk1)"),
            args.get("selection2", "(pk2)"),
            args.get("selection3", "(pk3)")
        )

    def _dihedral(args):
        return cmd.dihedral(
            args.get("name", "dihedral"),
            args.get("selection1", "(pk1)"),
            args.get("selection2", "(pk2)"),
            args.get("selection3", "(pk3)"),
            args.get("selection4", "(pk4)")
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

    def _save(args):
        state = args.get("state", "-1")
        try:
            state = int(state)
        except (ValueError, TypeError):
            state = -1
        return cmd.save(args.get("filename", ""), args.get("selection", "all"), state)

    def _png(args):
        return cmd.png(args.get("filename", "output.png"))

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
        return cmd.alter_state(
            state, args.get("selection", "all"), expression
        )

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
            args.get("selection", "all")
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
            args.get("selection", "all")
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
            args.get("prefix", "sym"),
            args.get("selection", "all"),
            cutoff
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

    # Build the dispatcher — only these commands are allowed
    dispatcher = {
        "show": _show,
        "hide": _hide,
        "color": _color,
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
        "util.cbc", "util.cbaw", "util.cbag", "util.cbac", "util.cbam",
        "util.cbay", "util.cbas", "util.cbab", "util.cbao", "util.cbap",
        "util.cbak", "util.chainbow", "util.rainbow",
        "util.color_by_element",
    ]
    for util_cmd in util_commands:
        dispatcher[util_cmd] = lambda args, name=util_cmd: _util_command(name, args)

    return dispatcher


##############################################################################
# SOCKET SERVER
##############################################################################

class SocketServer:
    def __init__(self, host='localhost', port=9876):
        self.host = host
        self.port = port
        self.socket = None
        self.client = None
        self.running = False
        self.thread = None
        self.command_callback = None

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
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            self.socket.settimeout(1.0)
        except OSError as e:
            _log(f"Could not listen on {self.host}:{self.port}: {e}")
            if self.socket:
                self.socket.close()
                self.socket = None
            return False

        self.command_callback = command_callback
        self.running = True
        self.thread = threading.Thread(target=self._run_server)
        self.thread.daemon = True
        self.thread.start()
        return True

    def _run_server(self):
        """Run the accept loop; the socket is already bound by start()."""
        try:
            _log(f"PyMOL MCP Socket server listening on {self.host}:{self.port}")

            while self.running:
                try:
                    self.client, address = self.socket.accept()
                    _log(f"Connected to client: {address}")
                    self.client.settimeout(1.0)

                    buffer = b''
                    while self.running:
                        try:
                            data = self.client.recv(4096)
                            if not data:
                                break

                            buffer += data

                            try:
                                command = json.loads(buffer.decode('utf-8'))
                                buffer = b''

                                result = self._handle_command(command)

                                failed = isinstance(result, dict) and (
                                    result.get("executed") is False
                                )
                                if failed:
                                    response = json.dumps({
                                        "status": "error",
                                        "message": result.get("error", "Unknown error")
                                    })
                                else:
                                    response = json.dumps({
                                        "status": "success",
                                        "result": result or "Command executed",
                                    })
                                self.client.sendall(response.encode('utf-8'))
                            except json.JSONDecodeError:
                                continue

                        except socket.timeout:
                            continue
                        except Exception as e:
                            _log(f"Error receiving data: {str(e)}")
                            break

                    if self.client:
                        self.client.close()
                        self.client = None
                except socket.timeout:
                    continue
                except Exception as e:
                    _log(f"Error accepting connection: {str(e)}")

        except Exception as e:
            _log(f"Socket server error: {str(e)}")
            if VERBOSE:
                traceback.print_exc()
        finally:
            if self.socket:
                self.socket.close()
            self.running = False
            _log("Socket server stopped")

    def _handle_command(self, command):
        """Handle received structured command"""
        if not command:
            return

        cmd_type = command.get("type", "")
        if cmd_type != "structured_command":
            return {"executed": False, "error": f"Unknown message type: {cmd_type}"}

        global received_commands
        cmd_name = command.get("command", "")
        args = command.get("args", {})
        source = command.get("source")
        received_commands.append(f"{cmd_name} {json.dumps(args)}")

        if self.command_callback and cmd_name:
            result = self.command_callback(cmd_name, args)
            _record_history(cmd_name, args, source, result)
            return result

    def stop(self):
        """Stop the socket server"""
        self.running = False
        if self.thread:
            self.thread.join(2.0)
        if self.client:
            self.client.close()
        if self.socket:
            self.socket.close()
        self.socket = None
        self.client = None
        self.thread = None


##############################################################################
# GUI
##############################################################################

def run_plugin_gui():
    '''
    Open our custom dialog
    '''
    global dialog

    if dialog is None:
        dialog = make_dialog()

    dialog.show()

def make_dialog():
    from pymol.Qt import QtWidgets
    from pymol.Qt.utils import loadUi

    dialog = QtWidgets.QDialog()

    uifile = os.path.join(os.path.dirname(__file__), 'pymol_mcp_plugin.ui')
    form = loadUi(uifile, dialog)

    # Reflect the current state — the server may already have been started
    # from `.pymolrc.py` before the dialog was ever opened.
    form.input_port.setValue(current_port)
    if listening:
        form.button_toggle_listening.setText("Stop Listening")
        update_status_label(form, f"Listening on port {current_port}")
    else:
        update_status_label(form, "Not listening")

    def toggle_listening():
        if not listening:
            if start_socket_server(form.input_port.value()):
                form.button_toggle_listening.setText("Stop Listening")
                update_status_label(form, f"Listening on port {current_port}")
        else:
            stop_socket_server()
            form.button_toggle_listening.setText("Start Listening")
            update_status_label(form, "Not listening")

    def close_dialog():
        stop_socket_server()
        dialog.close()

    form.button_toggle_listening.clicked.connect(toggle_listening)
    form.button_close.clicked.connect(close_dialog)

    return dialog

def update_status_label(form, text):
    """Update the status label with the given text"""
    form.label_status.setText(text)

    if "Not listening" in text:
        form.label_status.setStyleSheet("color: red;")
    elif "Listening" in text:
        form.label_status.setStyleSheet("color: green;")
    else:
        form.label_status.setStyleSheet("")
