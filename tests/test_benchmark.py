import json
import zipfile
from pathlib import Path

import pytest

from pymol_mcp.benchmark import (
    cleaned_replay,
    inspect_bundle,
    load_scenario,
    pymol_error_lines,
    score,
)

SCENARIO = Path("benchmarks/scenarios/barhl2_figure2b.json")


def test_public_scenario_contains_no_coordinate_path():
    scenario = load_scenario(SCENARIO)
    source = scenario["source"]
    assert source == {
        "kind": "public_accession",
        "accession": "8PMF",
        "object": "8pmf",
    }
    assert "/" not in json.dumps(source)


def test_bundle_privacy_accepts_metadata_only_archive(tmp_path):
    scenario = load_scenario(SCENARIO)
    bundle = tmp_path / "session.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        for name in scenario["privacy"]["archive_entries"]:
            archive.writestr(name, "reinitialize\n" if name == "replay.pml" else "{}")
    result = inspect_bundle(bundle, scenario)
    assert result["passed"] is True
    assert result["metrics"]["history_entries"] == 1


def test_bundle_privacy_rejects_embedded_coordinates(tmp_path):
    scenario = load_scenario(SCENARIO)
    bundle = tmp_path / "session.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        for name in scenario["privacy"]["archive_entries"]:
            archive.writestr(name, "reinitialize\n" if name == "replay.pml" else "{}")
        archive.writestr("private/model.cif", "sensitive")
    result = inspect_bundle(bundle, scenario)
    assert result["passed"] is False
    assert result["checks"]["no_embedded_molecular_files"] is False


def test_bundle_reports_aggregate_trajectory_metrics(tmp_path):
    scenario = load_scenario(SCENARIO)
    history = [
        {"command": "fetch", "ok": True, "replayable": True},
        {"command": "turn", "ok": True, "replayable": True},
        {"command": "png", "ok": True, "replayable": True},
        {"command": "show", "ok": False, "replayable": False},
    ]
    bundle = tmp_path / "session.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        for name in scenario["privacy"]["archive_entries"]:
            if name == "history.jsonl":
                value = "\n".join(json.dumps(item) for item in history)
            elif name == "replay.pml":
                value = "reinitialize\n"
            else:
                value = "{}"
            archive.writestr(name, value)
    assert inspect_bundle(bundle, scenario)["metrics"] == {
        "history_entries": 4,
        "failed_entries": 1,
        "replayable_entries": 3,
        "render_iterations": 1,
        "camera_changes": 1,
    }


def test_cleaned_replay_removes_writes_but_keeps_camera():
    replay = cleaned_replay(
        "reinitialize\nfetch 8pmf\nset_view (1,0,0)\n"
        "png /private/result.png, ray=1\nsave /private/result.pse\n"
    )
    assert replay == "reinitialize\nfetch 8pmf\nset_view (1,0,0)\n"


@pytest.mark.parametrize(
    "command",
    [
        "run evil.pml",
        "@evil.pml",
        "python",
        "alter all, b=1",
        "alter_state 1, all, x=0",
        "label all, name",
    ],
)
def test_cleaned_replay_rejects_code_loading(command):
    with pytest.raises(ValueError, match="unsafe replay command"):
        cleaned_replay(f"reinitialize\n{command}\n")


def test_cleaned_replay_rejects_chained_commands():
    with pytest.raises(ValueError, match="unsafe replay syntax"):
        cleaned_replay("reinitialize\nshow cartoon; run evil.pml\n")


def test_pymol_errors_are_found_even_when_process_status_would_be_zero():
    output = (
        'Selector-Error: Invalid selection name "8pmf".\n'
        " Scene: view updated.\n"
        "CmdException: selection failed\n"
        " Error: Failed to Create Object\n"
        " Error-fetch: unable to load '8pmf'.\n"
    )
    assert pymol_error_lines(output) == [
        'Selector-Error: Invalid selection name "8pmf".',
        "CmdException: selection failed",
        "Error: Failed to Create Object",
        "Error-fetch: unable to load '8pmf'.",
    ]


def test_normal_pymol_progress_is_not_an_error():
    assert pymol_error_lines('CmdLoad: "8pmf.cif" loaded as "8pmf".\n') == []


def test_score_separates_semantic_and_image_checks():
    scenario = load_scenario(SCENARIO)
    semantic = scenario["semantic"]
    state = {
        "objects": semantic["objects"],
        "named_selections": semantic["named_selections"],
        "selections": {
            item["id"]: item["atoms"] for item in semantic["selections"]
        },
        "view": semantic["view_rotation"] + [0.0] * 9,
        "version": ["3.1.0"],
    }
    image = {
        "width": scenario["image"]["width"],
        "height": scenario["image"]["height"],
        "opaque": True,
        "white_corner": True,
    }
    result = score(scenario, state, image, {"passed": True})
    assert result["passed"] is True
    assert result["score"] == 1.0
