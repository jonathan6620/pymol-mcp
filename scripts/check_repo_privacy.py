#!/usr/bin/env python3
"""Fail when Git tracks molecular data or raw PyMOL session artifacts."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pymol_mcp.privacy import find_sensitive_paths


def git_paths(*args: str) -> list[str]:
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    )
    return [line for line in proc.stdout.splitlines() if line]


def main() -> int:
    paths = set(git_paths("ls-files"))
    paths.update(git_paths("diff", "--cached", "--name-only", "--diff-filter=ACMR"))
    sensitive = find_sensitive_paths(list(paths), root=Path.cwd())
    if sensitive:
        print("Refusing repository content that may contain molecular/session data:")
        for path in sensitive:
            print(f"  {path}")
        print("Keep it in benchmarks/private or benchmark-results, which are ignored.")
        return 1
    print("Repository privacy check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
