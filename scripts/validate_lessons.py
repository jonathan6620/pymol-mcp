#!/usr/bin/env python3
"""Validate public lesson packages and their referenced scenarios."""

from __future__ import annotations

import json
from pathlib import Path

from pymol_mcp.benchmark import load_scenario
from pymol_mcp.lessons import load_lesson
from pymol_mcp.privacy import public_payload_violations


def main() -> int:
    root = Path("benchmarks")
    errors = []
    scenarios = {}
    for path in sorted((root / "scenarios").glob("*.json")):
        try:
            scenario = load_scenario(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        violations = public_payload_violations(scenario)
        if violations:
            errors.append(f"{path}: sensitive values: {'; '.join(violations)}")
            continue
        scenarios[scenario["id"]] = scenario
    lesson_paths = sorted((root / "lessons").glob("*.json"))
    for path in lesson_paths:
        try:
            lesson = load_lesson(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        for scenario in lesson["scenarios"]:
            if scenario not in scenarios:
                errors.append(f"{path}: unknown scenario {scenario!r}")
                continue
            source_kind = scenarios[scenario].get("source", {}).get("kind")
            expected_kind = {
                "public_fixture": "public_accession",
                "synthetic_fixture": "synthetic",
            }.get(lesson["visibility"])
            if expected_kind is not None and source_kind != expected_kind:
                errors.append(
                    f"{path}: {lesson['visibility']} requires scenario source "
                    f"kind {expected_kind!r}, got {source_kind!r}"
                )
    if not lesson_paths:
        errors.append("no lesson packages found")
    if errors:
        print("Lesson validation failed:")
        for error in errors:
            print(f"  {error}")
        return 1
    print(f"Validated {len(lesson_paths)} lesson package(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
