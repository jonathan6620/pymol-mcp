"""Install the PyMOL usage skill into Claude Code's user skill directory.

    uv run python install_skill.py

Symlinks skills/pymol-mcp into ~/.claude/skills/ so `git pull` keeps it current,
falling back to a copy where symlinks are unavailable (Windows without Developer
Mode). Rerun after each pull in that case.

User scope rather than this repo's .claude/skills/ on purpose: the MCP server is
installed with `claude mcp add pymol -s user`, so PyMOL gets driven from whatever
project directory the user's structures live in. A repo-scoped skill would only
be discovered while working inside this checkout.
"""

import os
import shutil
import sys
import time

SKILL_NAME = "pymol-mcp"
COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".DS_Store", "*.pyc")


def source_dir():
    """Absolute path to the skill in this checkout."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source = os.path.join(repo, "skills", SKILL_NAME)
    if not os.path.isfile(os.path.join(source, "SKILL.md")):
        sys.exit(f"error: no SKILL.md found at {source}")
    return source


def dest_dir():
    """~/.claude/skills/<name>, creating the parent if Claude Code has not."""
    parent = os.path.join(os.path.expanduser("~"), ".claude", "skills")
    os.makedirs(parent, exist_ok=True)
    return os.path.join(parent, SKILL_NAME)


def clear(dest):
    """Remove a previous install, preserving anything hand-edited."""
    if os.path.islink(dest):
        os.unlink(dest)
        return None
    if os.path.isdir(dest):
        # A real directory here is not ours; it may hold local edits.
        saved = f"{dest}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.move(dest, saved)
        return saved
    return None


def main():
    source = source_dir()
    dest = dest_dir()

    if os.path.islink(dest) and os.path.realpath(dest) == os.path.realpath(source):
        print(f"Already linked: {dest} -> {source}")
        return

    saved = clear(dest)
    if saved:
        print(f"Moved previous skill to {saved}")

    try:
        os.symlink(source, dest, target_is_directory=True)
        print(f"Installed: {dest}\n  -> symlink to {source}")
        print("`git pull` now updates the skill automatically.")
    except (OSError, NotImplementedError, AttributeError):
        shutil.copytree(source, dest, ignore=COPY_IGNORE)
        print(f"Installed: {dest}\n  -> copy of {source} (symlinks unavailable)")
        print("Rerun this script after each `git pull`.")

    print("Start a new Claude Code session to pick it up.")


if __name__ == "__main__":
    main()
