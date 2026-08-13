import copy
import json
from pathlib import Path

import pytest

from pymol_mcp.lessons import (
    compare_results,
    load_lesson,
    main,
    public_payload_violations,
    public_result,
    validate_lesson,
)

LESSON = Path("benchmarks/lessons/barhl2_figure2b.json")


def test_barhl2_lesson_is_valid_and_contains_no_raw_artifacts():
    lesson = load_lesson(LESSON)
    assert lesson["visibility"] == "public_fixture"
    assert len(lesson["findings"]) == 5
    assert public_payload_violations(lesson) == []


def test_public_lesson_rejects_private_artifacts():
    lesson = load_lesson(LESSON)
    lesson["privacy"]["raw_session_included"] = True
    with pytest.raises(ValueError, match="private artifacts"):
        validate_lesson(lesson)


def test_public_lesson_rejects_paths_even_when_privacy_flags_are_clean():
    lesson = load_lesson(LESSON)
    lesson["provenance"]["notes"] = "/Users/researcher/private/model.cif"
    with pytest.raises(ValueError, match="sensitive values"):
        validate_lesson(lesson)


def test_private_evaluation_lesson_cannot_enter_public_packages():
    lesson = load_lesson(LESSON)
    lesson["visibility"] = "private_evaluation_only"
    lesson["provenance"]["source_policy"] = "private"
    with pytest.raises(ValueError, match="cannot be published"):
        validate_lesson(lesson, public=True)
    validate_lesson(lesson, public=False)


def test_public_result_is_an_allowlisted_projection():
    detailed = {
        "schema_version": 1,
        "scenario": "barhl2-figure-2b",
        "passed": True,
        "score": 1,
        "checks": {"objects": True},
        "metrics": {
            "view_rotation_max_error": 0.01,
            "trajectory": {"history_entries": 90, "private_note": "secret"},
            "width": 1200,
            "height": 990,
            "opaque": True,
            "private_render_path": "/private/result.png",
        },
        "runtime": {"pymol_version": "3.1.0", "host": "private-host"},
        "raw": {"session": "/private/session.zip"},
    }
    result = public_result(detailed, "publication-barhl2-figure2b")
    assert result["metrics"]["trajectory"] == {"history_entries": 90}
    assert result["metrics"]["image"] == {
        "height": 990,
        "opaque": True,
        "width": 1200,
    }
    assert "raw" not in result
    assert "host" not in result["runtime"]
    assert public_payload_violations(result) == []


def test_compare_results_reports_improvements_and_regressions():
    baseline = {
        "scenario": "fixture",
        "score": 0.5,
        "checks": {"camera": False, "ribbon": True},
        "metrics": {"trajectory": {"render_iterations": 8}},
    }
    candidate = copy.deepcopy(baseline)
    candidate["score"] = 0.75
    candidate["checks"] = {"camera": True, "ribbon": False}
    candidate["metrics"]["trajectory"]["render_iterations"] = 5
    comparison = compare_results(baseline, candidate)
    assert comparison["score_delta"] == 0.25
    assert comparison["improved_checks"] == ["camera"]
    assert comparison["regressed_checks"] == ["ribbon"]
    assert comparison["trajectory_deltas"] == {"render_iterations": -3}


def test_compare_results_rejects_different_scenarios():
    with pytest.raises(ValueError, match="different scenarios"):
        compare_results(
            {"scenario": "a", "score": 1, "checks": {}},
            {"scenario": "b", "score": 1, "checks": {}},
        )


def test_compare_results_rejects_sensitive_scenario_name():
    result = {"scenario": "/private/model", "score": 1, "checks": {}}
    with pytest.raises(ValueError, match="sensitive values"):
        compare_results(result, copy.deepcopy(result))


def test_cli_publishes_only_allowlisted_result(tmp_path):
    detailed = {
        "scenario": "barhl2-figure-2b",
        "passed": True,
        "score": 1,
        "checks": {"scene": True},
        "metrics": {
            "trajectory": {"failed_entries": 0},
            "width": 320,
            "private_path": "/private/render.png",
        },
        "runtime": {"pymol_version": "3.1.0"},
    }
    source = tmp_path / "detailed.json"
    output = tmp_path / "public.json"
    source.write_text(json.dumps(detailed))
    assert (
        main(
            [
                "publish",
                str(source),
                "--lesson",
                str(LESSON),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    text = output.read_text()
    assert "publication-barhl2-figure2b" in text
    assert "private_path" not in text
    assert "/private/render.png" not in text


def test_cli_rejects_result_not_covered_by_lesson(tmp_path):
    source = tmp_path / "detailed.json"
    source.write_text(
        json.dumps(
            {"scenario": "other", "passed": True, "score": 1, "checks": {}}
        )
    )
    with pytest.raises(ValueError, match="does not cover scenario"):
        main(["publish", str(source), "--lesson", str(LESSON)])
