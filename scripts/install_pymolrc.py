"""Install the MCP socket auto-start snippet into the user's pymolrc.

    uv run python install_pymolrc.py [--force]

Writes a marked block into ~/.pymolrc.py so the socket listener starts whenever
PyMOL opens. The block is delimited by markers, so rerunning this updates that
block in place and leaves the rest of the file alone.

Deliberately NOT named `pymolrc*`: PyMOL's get_user_config() scans the current
working directory before $HOME and stops at the first directory containing a
match, so a file matching that pattern in this repo would shadow the user's real
pymolrc whenever PyMOL was launched from here.
"""

import argparse
import os
import shutil
import sys
import time

BEGIN = "# >>> pymol-mcp auto-start >>>"
END = "# <<< pymol-mcp auto-start <<<"

BLOCK = f'''{BEGIN}
# Managed by install_pymolrc.py -- edits inside this block are overwritten.
import importlib, threading, time

# PyMOL imports plugins from its startup directory under this name, so there is
# no path to configure. Requires the plugin to be installed.
PLUGIN_MODULE = "pmg_tk.startup.pymol-mcp-socket-plugin"


def _auto_start_mcp_socket():
    time.sleep(3)  # let PyMOL's plugin system finish initializing
    try:
        plugin = importlib.import_module(PLUGIN_MODULE)
    except ImportError:
        print(
            "MCP socket plugin not installed -- run: "
            "pymol -cq scripts/install_plugin.py"
        )
        return
    try:
        # No port argument: each PyMOL claims the first free one in the
        # plugin's range, so a second instance gets its own listener instead
        # of silently having none. The MCP server finds them by scanning.
        if plugin.start_socket_server():
            print(f"MCP socket plugin auto-started on port {{plugin.current_port}}")
        else:
            print("MCP socket listener not started; every port in range is in use.")
    except Exception as e:
        print(f"MCP socket auto-start failed: {{e}}")


threading.Thread(target=_auto_start_mcp_socket, daemon=True).start()
{END}
'''


def target_path():
    """~/.pymolrc.py -- the $HOME entry of PyMOL's own config search."""
    return os.path.join(os.path.expanduser("~"), ".pymolrc.py")


def backup(path):
    dest = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, dest)
    return dest


def splice(existing):
    """Replace the managed block, or append it. Returns (text, action)."""
    start = existing.find(BEGIN)
    if start == -1:
        sep = "" if existing.endswith("\n") or not existing else "\n"
        return existing + sep + "\n" + BLOCK, "appended block to"

    stop = existing.find(END, start)
    if stop == -1:
        raise SystemExit(
            f"error: found {BEGIN!r} but no closing {END!r}.\n"
            "Repair or remove that block by hand, then rerun."
        )
    tail = existing[stop + len(END) + 1 :]
    return existing[:start] + BLOCK + tail, "updated block in"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--force",
        action="store_true",
        help="proceed even if an unmanaged auto-start snippet is already present",
    )
    args = ap.parse_args()

    path = target_path()

    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(BLOCK)
        print(f"Created {path}")
        print("Start PyMOL; it will report the port its listener claimed.")
        return

    with open(path) as fh:
        existing = fh.read()

    # An older hand-pasted snippet has no markers, so splice() would append a
    # second copy and both would race for the port.
    if BEGIN not in existing and "start_socket_server" in existing:
        if not args.force:
            sys.exit(
                f"error: {path} already contains an auto-start snippet that this\n"
                "script does not manage. Appending would start the listener twice.\n\n"
                "Remove the old snippet and rerun, or rerun with --force to replace\n"
                "the whole file (a timestamped backup is kept either way):\n\n"
                "    make install-pymolrc FORCE=1\n"
            )
        saved = backup(path)
        with open(path, "w") as fh:
            fh.write(BLOCK)
        print(f"Backed up previous config to {saved}")
        print(f"Replaced {path}")
        print("Restart PyMOL to pick it up.")
        return

    updated, action = splice(existing)
    if updated == existing:
        print(f"Already up to date: {path}")
        return

    saved = backup(path)
    with open(path, "w") as fh:
        fh.write(updated)
    print(f"Backed up previous config to {saved}")
    print(f"{action.capitalize()} {path}")
    print("Restart PyMOL to pick it up.")


if __name__ == "__main__":
    main()
