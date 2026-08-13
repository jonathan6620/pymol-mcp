"""Validated, non-sensitive lessons distilled from local PyMOL sessions.

Raw sessions are intentionally outside this module.  A lesson describes what
was learned, points at a public or synthetic benchmark, and carries only the
aggregate result fields that are safe to review or share.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from pymol_mcp.privacy import public_payload_violations

LESSON_VISIBILITIES = frozenset(
    {"public_fixture", "synthetic_fixture", "private_evaluation_only"}
)
FINDING_COMPONENTS = frozenset({"skill", "mcp", "replay", "evaluation"})
PUBLIC_TRAJECTORY_METRICS = frozenset(
    {
        "history_entries",
        "failed_entries",
        "replayable_entries",
        "render_iterations",
        "camera_changes",
        "user_corrections",
    }
)
PUBLIC_IMAGE_METRICS = frozenset(
    {
        "width",
        "height",
        "opaque",
        "white_corner",
        "reference_mean_absolute_error",
        "reference_rms",
    }
)
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
def validate_lesson(lesson: dict[str, Any], *, public: bool = True) -> None:
    """Validate one version-1 session lesson, raising ``ValueError`` on failure."""
    if lesson.get("schema_version") != 1:
        raise ValueError("unsupported lesson schema_version")
    identifier = lesson.get("id")
    if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
        raise ValueError("lesson id must be a lowercase slug")
    visibility = lesson.get("visibility")
    if visibility not in LESSON_VISIBILITIES:
        raise ValueError("invalid lesson visibility")
    if public and visibility == "private_evaluation_only":
        raise ValueError("private_evaluation_only lessons cannot be published")

    provenance = lesson.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("lesson requires provenance")
    source_policy = provenance.get("source_policy")
    if visibility == "public_fixture" and source_policy != "public":
        raise ValueError("public_fixture lessons require a public source policy")
    if visibility == "synthetic_fixture" and source_policy != "synthetic":
        raise ValueError("synthetic_fixture lessons require a synthetic source policy")

    scenarios = lesson.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("lesson requires at least one scenario")
    if not all(
        isinstance(item, str) and _IDENTIFIER.fullmatch(item) for item in scenarios
    ):
        raise ValueError("scenario ids must be lowercase slugs")

    findings = lesson.get("findings")
    if not isinstance(findings, list) or not findings:
        raise ValueError("lesson requires at least one finding")
    finding_ids = []
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("each finding must be an object")
        finding_id = finding.get("id")
        if not isinstance(finding_id, str) or not _IDENTIFIER.fullmatch(finding_id):
            raise ValueError("finding id must be a lowercase slug")
        finding_ids.append(finding_id)
        if finding.get("component") not in FINDING_COMPONENTS:
            raise ValueError(f"invalid finding component for {finding_id}")
        if not finding.get("symptom") or not finding.get("correction"):
            raise ValueError(f"finding {finding_id} requires symptom and correction")
    if len(finding_ids) != len(set(finding_ids)):
        raise ValueError("finding ids must be unique")

    privacy = lesson.get("privacy")
    if not isinstance(privacy, dict):
        raise ValueError("lesson requires a privacy declaration")
    for field in (
        "raw_session_included",
        "molecular_files_included",
        "user_prompts_included",
        "rendered_images_included",
        "reviewed_for_release",
    ):
        if not isinstance(privacy.get(field), bool):
            raise ValueError(f"privacy.{field} must be boolean")
    if public:
        included = [
            field
            for field in (
                "raw_session_included",
                "molecular_files_included",
                "user_prompts_included",
                "rendered_images_included",
            )
            if privacy[field]
        ]
        if included:
            raise ValueError(
                "public lesson includes private artifacts: " + ", ".join(included)
            )
        if not privacy["reviewed_for_release"]:
            raise ValueError("public lesson must be reviewed for release")
        violations = public_payload_violations(lesson)
        if violations:
            raise ValueError(
                "public lesson contains sensitive values: "
                + "; ".join(violations)
            )


def load_lesson(path: Path, *, public: bool = True) -> dict[str, Any]:
    lesson = json.loads(path.read_text())
    validate_lesson(lesson, public=public)
    return lesson


def public_result(
    result: dict[str, Any], lesson_id: str | None = None
) -> dict[str, Any]:
    """Project a detailed local benchmark result onto an explicit public allowlist."""
    trajectory = result.get("metrics", {}).get("trajectory", {})
    image = result.get("metrics", {})
    projected: dict[str, Any] = {
        "schema_version": 1,
        "scenario": result["scenario"],
        "passed": bool(result["passed"]),
        "score": float(result["score"]),
        "checks": {
            str(key): bool(value) for key, value in result.get("checks", {}).items()
        },
        "metrics": {
            "trajectory": {
                key: trajectory[key]
                for key in sorted(PUBLIC_TRAJECTORY_METRICS & trajectory.keys())
            },
            "image": {
                key: image[key]
                for key in sorted(PUBLIC_IMAGE_METRICS & image.keys())
            },
        },
        "runtime": {
            "pymol_version": str(result.get("runtime", {}).get("pymol_version", ""))
        },
    }
    if lesson_id is not None:
        if not _IDENTIFIER.fullmatch(lesson_id):
            raise ValueError("lesson id must be a lowercase slug")
        projected["lesson"] = lesson_id
    violations = public_payload_violations(projected)
    if violations:
        raise ValueError(
            "public result contains sensitive values: " + "; ".join(violations)
        )
    return projected


def compare_results(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    """Compare two public results without requiring their private diagnostics."""
    if baseline.get("scenario") != candidate.get("scenario"):
        raise ValueError("cannot compare results from different scenarios")
    before = baseline.get("checks", {})
    after = candidate.get("checks", {})
    names = sorted(set(before) | set(after))
    changes = {
        name: {"baseline": bool(before.get(name)), "candidate": bool(after.get(name))}
        for name in names
        if bool(before.get(name)) != bool(after.get(name))
    }
    before_trajectory = baseline.get("metrics", {}).get("trajectory", {})
    after_trajectory = candidate.get("metrics", {}).get("trajectory", {})
    trajectory_names = sorted(
        PUBLIC_TRAJECTORY_METRICS
        & before_trajectory.keys()
        & after_trajectory.keys()
    )
    comparison = {
        "schema_version": 1,
        "scenario": baseline["scenario"],
        "score_delta": float(candidate["score"]) - float(baseline["score"]),
        "improved_checks": sorted(
            name for name, values in changes.items() if values["candidate"]
        ),
        "regressed_checks": sorted(
            name for name, values in changes.items() if not values["candidate"]
        ),
        "changes": changes,
        "trajectory_deltas": {
            name: after_trajectory[name] - before_trajectory[name]
            for name in trajectory_names
            if isinstance(before_trajectory[name], (int, float))
            and not isinstance(before_trajectory[name], bool)
            and isinstance(after_trajectory[name], (int, float))
            and not isinstance(after_trajectory[name], bool)
        },
    }
    violations = public_payload_violations(comparison)
    if violations:
        raise ValueError(
            "public comparison contains sensitive values: "
            + "; ".join(violations)
        )
    return comparison


def _write_result(value: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(rendered, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate a lesson package")
    validate.add_argument("lesson", type=Path)

    publish = subparsers.add_parser(
        "publish", help="create an allowlisted public benchmark result"
    )
    publish.add_argument("result", type=Path)
    publish.add_argument("--lesson", required=True, type=Path)
    publish.add_argument("--output", type=Path)

    compare = subparsers.add_parser(
        "compare", help="compare baseline and candidate public results"
    )
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--output", type=Path)

    args = parser.parse_args(argv)
    if args.command == "validate":
        lesson = load_lesson(args.lesson)
        print(f"Validated lesson {lesson['id']}.")
        return 0
    if args.command == "publish":
        detailed = json.loads(args.result.read_text())
        lesson = load_lesson(args.lesson)
        if detailed.get("scenario") not in lesson["scenarios"]:
            raise ValueError(
                f"lesson {lesson['id']} does not cover scenario "
                f"{detailed.get('scenario')!r}"
            )
        _write_result(public_result(detailed, lesson["id"]), args.output)
        return 0
    baseline = json.loads(args.baseline.read_text())
    candidate = json.loads(args.candidate.read_text())
    _write_result(compare_results(baseline, candidate), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
