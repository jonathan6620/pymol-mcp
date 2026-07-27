"""Register this checkout's MCP server with Claude Code and OpenAI Codex.

    uv run python install_mcp.py

Both CLIs are optional: a missing one is reported and skipped, not an error.

Registration is *reconciled*, not merely created. An entry that already exists
is compared against the command this checkout needs, and replaced when it does
not match. That matters because `mcp get` answers "is there an entry", never "is
the entry correct", and the two came apart when the server moved from a flat
pymol_mcp_server.py to the pymol-mcp console script: every client still had a
registration, each one naming a file that no longer existed. `uv` failed to
spawn it, the failure surfaced only as tools silently missing from the session,
and rerunning the installer printed "already registered" and changed nothing.

So the check has to be on the command, not on the name. Reconciling also makes
the script safe to rerun after moving the checkout, which is the other way the
recorded --directory path goes stale.
"""

import json
import os
import shutil
import subprocess
import sys

SERVER_NAME = "pymol"

# Present: the entry is there and correct. Absent: no entry. Stale: an entry
# naming some other command, which is the case that used to go undetected.
PRESENT, ABSENT, STALE = "present", "absent", "stale"


def repo_root():
    """Absolute path to this checkout."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def expected_argv(root):
    """The command that starts the server from `root`.

    `uv --directory` rather than a venv python: it resolves the entry point
    through the project's own environment, so the registration keeps working
    after a dependency change without pointing into .venv internals.

    `--frozen` for the same reason the Makefile exports UV_FROZEN: a uv older
    than the one that wrote uv.lock rewrites the file in place. The Makefile's
    export does not reach here -- the client (Claude Code, Codex) spawns this
    command itself, with its own environment -- so the flag has to be baked
    into the registration. Without it the lock churns on every client start,
    which is how it kept coming back after the Makefile was fixed.
    """
    return ["uv", "--directory", root, "run", "--frozen", "pymol-mcp"]


def run(argv):
    """Run a CLI, returning (exit code, stdout+stderr). Never raises."""
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, check=False, timeout=120
        )
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def parse_claude(text):
    """Pull (command, args string) out of `claude mcp get` human-readable output.

    There is no --json on `claude mcp get`, so this reads the labelled lines:

        Type: stdio
        Command: uv
        Args: --directory /path/to/repo run --frozen pymol-mcp

    Args is returned as the single joined string it is printed as, and compared
    that way. Splitting it would be guesswork on any path containing a space --
    the output gives no quoting to recover the original argv from.
    """
    command = args = None
    for line in text.splitlines():
        label, _, value = line.strip().partition(":")
        if label == "Command":
            command = value.strip()
        elif label == "Args":
            args = value.strip()
    if command is None:
        return None
    return command, args or ""


def parse_codex(text):
    """Pull (command, args string) out of `codex mcp get --json` output.

    The command lives under "transport", not at the top level:

        {"name": "pymol", "enabled": true,
         "transport": {"type": "stdio", "command": "uv", "args": [...]}}

    Reading the top level instead makes every entry look unparseable, which
    this script treats as stale -- so it removed and re-added the server on
    every run and quietly stopped being idempotent. The top level is still
    accepted as a fallback in case the shape flattens again.
    """
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    transport = data.get("transport")
    if isinstance(transport, dict):
        data = transport
    command = data.get("command")
    if not isinstance(command, str):
        return None
    args = data.get("args") or []
    if not isinstance(args, list):
        return None
    return command, " ".join(str(a) for a in args)


class Client:
    """One MCP client CLI and the argv shapes it wants."""

    def __init__(self, cli, label, get_argv, add_argv, remove_argv, parse):
        self.cli = cli
        self.label = label
        self._get = get_argv
        self._add = add_argv
        self._remove = remove_argv
        self._parse = parse

    def available(self):
        return shutil.which(self.cli) is not None

    def state(self, root):
        """Classify the existing registration as present, absent or stale.

        Unparseable output counts as stale on purpose. Re-registering is
        idempotent, so converging on the right command beats preserving
        something we cannot read -- and a parse that breaks on a future CLI
        version should degrade into "fix it", not into "assume it is fine".
        """
        code, text = run(self._get(SERVER_NAME))
        if code != 0:
            return ABSENT, None
        parsed = self._parse(text)
        if parsed is None:
            return STALE, None
        command, args = parsed
        want = expected_argv(root)
        if command == want[0] and args == " ".join(want[1:]):
            return PRESENT, f"{command} {args}".strip()
        return STALE, f"{command} {args}".strip()

    def reconcile(self, root):
        """Make the registration match this checkout. Returns a status string."""
        state, found = self.state(root)
        if state == PRESENT:
            return f"{self.label}: already registered correctly."

        if state == STALE:
            # Remove first: `mcp add` refuses to overwrite an existing name.
            run(self._remove(SERVER_NAME))

        code, text = run(self._add(SERVER_NAME, expected_argv(root)))
        if code != 0:
            detail = text.strip().splitlines()
            return (
                f"{self.label}: FAILED to register -- "
                f"{detail[-1] if detail else 'no output'}"
            )
        if state == STALE:
            where = f" (was: {found})" if found else " (previous entry unreadable)"
            return f"{self.label}: replaced a stale registration{where}."
        return f"{self.label}: registered."


CLIENTS = [
    Client(
        cli="claude",
        label="Claude Code",
        # User scope, so the server is available from whatever project directory
        # holds the user's structures rather than only inside this checkout.
        get_argv=lambda name: ["claude", "mcp", "get", name],
        add_argv=lambda name, argv: (
            ["claude", "mcp", "add", name, "-s", "user", "--"] + argv
        ),
        remove_argv=lambda name: ["claude", "mcp", "remove", name, "-s", "user"],
        parse=parse_claude,
    ),
    Client(
        cli="codex",
        label="Codex",
        get_argv=lambda name: ["codex", "mcp", "get", name, "--json"],
        add_argv=lambda name, argv: ["codex", "mcp", "add", name, "--"] + argv,
        remove_argv=lambda name: ["codex", "mcp", "remove", name],
        parse=parse_codex,
    ),
]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--skip-clients" in argv:
        print("Skipping MCP client registration (--skip-clients).")
        return 0

    root = repo_root()
    command = " ".join(expected_argv(root))
    print(f"Reconciling the '{SERVER_NAME}' MCP server against: {command}")

    available = [c for c in CLIENTS if c.available()]
    for client in available:
        print("  " + client.reconcile(root))

    missing = [c.label for c in CLIENTS if not c.available()]
    if missing:
        print(f"  Not on PATH, skipped: {', '.join(missing)}")

    if not available:
        print(
            "\nNo MCP client CLI was found. Register manually with one of:\n"
            f"    claude mcp add {SERVER_NAME} -s user -- {command}\n"
            f"    codex mcp add {SERVER_NAME} -- {command}\n"
            "For Claude Desktop, see the README (Step 3, Option A)."
        )
        return 0

    print("Start a new Claude Code or Codex session to pick it up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
