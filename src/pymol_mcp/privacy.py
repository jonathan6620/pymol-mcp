"""Repository guards for molecular data and raw local-session artifacts."""

from __future__ import annotations

import fnmatch
import re
import zipfile
from pathlib import Path
from typing import Any

SENSITIVE_SUFFIXES = frozenset(
    {
        ".bcif",
        ".cif",
        ".dcd",
        ".ent",
        ".mae",
        ".map",
        ".mmcif",
        ".mol2",
        ".mrc",
        ".nc",
        ".pdb",
        ".pse",
        ".sdf",
        ".trr",
        ".xtc",
    }
)
SENSITIVE_NAMES = frozenset({"history.jsonl"})
SENSITIVE_PATTERNS = ("session-*.pml", "*_replay.zip")
_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_SENSITIVE_FILE = re.compile(
    r"(?i)(?:^|[\\/\s])[^\s]+(?:"
    + "|".join(re.escape(suffix) for suffix in sorted(SENSITIVE_SUFFIXES))
    + r")(?=$|[\s,;])"
)


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def public_payload_violations(value: Any) -> list[str]:
    """Return local paths and sensitive filenames in shareable metadata."""
    violations = []
    for text in _strings(value):
        stripped = text.strip()
        if (
            stripped.startswith(("/", "~/", "file://"))
            or _WINDOWS_PATH.match(stripped)
            or "/Users/" in stripped
            or "/home/" in stripped
        ):
            violations.append(f"absolute path: {text}")
        elif _SENSITIVE_FILE.search(stripped):
            violations.append(f"molecular/session filename: {text}")
    return sorted(set(violations))


def is_sensitive_path(path: str | Path) -> bool:
    candidate = Path(path)
    name = candidate.name.lower()
    if candidate.suffix.lower() in SENSITIVE_SUFFIXES or name in SENSITIVE_NAMES:
        return True
    return any(fnmatch.fnmatch(name, pattern) for pattern in SENSITIVE_PATTERNS)


def archive_contains_sensitive_payload(path: Path) -> bool:
    """Recognize exported sessions and molecular payloads regardless of ZIP name."""
    if path.suffix.lower() != ".zip" or not path.is_file():
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False
    session_entries = {"history.jsonl", "replay.pml", "final-state.json"}
    basenames = {Path(name).name.lower() for name in names}
    return session_entries <= basenames or any(
        is_sensitive_path(name) for name in names
    )


def find_sensitive_paths(
    paths: list[str], *, root: Path | None = None
) -> list[str]:
    base = root or Path.cwd()
    return sorted(
        path
        for path in paths
        if is_sensitive_path(path)
        or archive_contains_sensitive_payload(base / path)
    )
