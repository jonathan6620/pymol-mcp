"""Install the PyMOL MCP socket plugin into PyMOL's plugin directory.

Run with PyMOL's own interpreter -- it is what knows where plugins live, so
nothing here is hardcoded to a particular OS or PyMOL distribution:

    pymol -cq install_plugin.py

PyMOL's Plugin Manager installs a *copy* of the plugin. That copy silently
drifts out of date every time you `git pull`, and a stale copy fails in
confusing ways (an older one predates `start_socket_server`, so auto-start
reports the plugin as missing). This script symlinks the checkout instead, so
the plugin PyMOL loads is always the one in this repository. Where symlinks
aren't available (Windows without Developer Mode) it falls back to a copy and
says so -- rerun it after each pull in that case.
"""

import importlib
import os
import shutil
import sys

PLUGIN_DIRNAME = "pymol-mcp-socket-plugin"
MODULE_NAME = "pmg_tk.startup." + PLUGIN_DIRNAME
COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".DS_Store", "*.pyc")


def repo_plugin_dir():
    """Absolute path to the plugin source in this checkout."""
    # Under `pymol -cq script.py`, __file__ is PyMOL's own __init__.py; the
    # script path arrives as __script__ instead.
    script = globals().get("__script__") or globals().get("__file__")
    if not script:
        sys.exit("error: cannot determine this script's location")

    source = os.path.join(os.path.dirname(os.path.abspath(script)), PLUGIN_DIRNAME)
    if not os.path.isdir(source):
        sys.exit(f"error: plugin source not found at {source}")
    return source


def startup_dir():
    """First writable directory in PyMOL's plugin search path."""
    try:
        from pymol.plugins import get_startup_path
    except ImportError:
        sys.exit(
            "error: this script must run inside PyMOL, which is what knows\n"
            "where plugins are installed. Run it as:\n\n"
            "    pymol -cq install_plugin.py\n"
        )

    candidates = get_startup_path()
    for path in candidates:
        if os.path.isdir(path) and os.access(path, os.W_OK):
            return path

    sys.exit(
        "error: no writable plugin directory found. Tried:\n  "
        + "\n  ".join(candidates)
        + "\n\nInstall PyMOL somewhere you own (e.g. a conda env), or rerun\n"
        "with permission to write to one of the paths above."
    )


def remove_existing(dest):
    """Clear any previous install, symlink or copy."""
    if os.path.islink(dest) or os.path.isfile(dest):
        os.unlink(dest)
        return True
    if os.path.isdir(dest):
        shutil.rmtree(dest)
        return True
    return False


def verify(dest):
    """Import the plugin the way PyMOL does and check it is usable."""
    importlib.invalidate_caches()
    try:
        plugin = importlib.import_module(MODULE_NAME)
    except Exception as exc:  # noqa: BLE001 - report whatever import raised
        sys.exit(f"error: installed to {dest} but importing it failed: {exc}")

    if not hasattr(plugin, "start_socket_server"):
        sys.exit(
            f"error: the plugin at {dest} has no start_socket_server(). This is\n"
            "the signature of an outdated copy shadowing the install; remove it\n"
            "and rerun."
        )


def main():
    source = repo_plugin_dir()
    dest = os.path.join(startup_dir(), PLUGIN_DIRNAME)

    if os.path.realpath(dest) == os.path.realpath(source):
        print(f"Already linked: {dest} -> {source}")
        verify(dest)
        print("Plugin verified. Start PyMOL and it will be available.")
        return

    replaced = remove_existing(dest)

    linked = True
    try:
        os.symlink(source, dest, target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        linked = False
        shutil.copytree(source, dest, ignore=COPY_IGNORE)

    verify(dest)

    action = "Replaced" if replaced else "Installed"
    if linked:
        print(f"{action}: {dest}\n  -> symlink to {source}")
        print("Plugin verified. `git pull` now updates PyMOL's plugin automatically.")
    else:
        print(f"{action}: {dest}\n  -> copy of {source} (symlinks unavailable)")
        print("Plugin verified. Rerun this script after each `git pull`.")

    print("Restart PyMOL if it is already running.")


main()
