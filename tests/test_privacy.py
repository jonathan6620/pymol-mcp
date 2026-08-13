import zipfile

from pymol_mcp.privacy import (
    archive_contains_sensitive_payload,
    find_sensitive_paths,
    is_sensitive_path,
    public_payload_violations,
)


def test_molecular_and_raw_session_files_are_sensitive():
    for path in (
        "private/model.pdb",
        "private/model.cif",
        "scene.pse",
        "history.jsonl",
        "session-20260813-1.pml",
        "run_replay.zip",
    ):
        assert is_sensitive_path(path), path


def test_code_and_public_scenario_metadata_are_allowed():
    for path in (
        "src/pymol_mcp/server.py",
        "benchmarks/scenarios/barhl2_figure2b.json",
        "benchmarks/README.md",
        "scripts/build_figure.pml",
    ):
        assert not is_sensitive_path(path), path


def test_sensitive_paths_are_sorted_for_stable_ci_output():
    paths = ["z/model.pdb", "README.md", "a/history.jsonl"]
    assert find_sensitive_paths(paths) == ["a/history.jsonl", "z/model.pdb"]


def test_renamed_session_zip_is_detected_by_contents(tmp_path):
    bundle = tmp_path / "innocent-name.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("history.jsonl", "{}\n")
        archive.writestr("replay.pml", "reinitialize\n")
        archive.writestr("final-state.json", "{}")
    assert archive_contains_sensitive_payload(bundle) is True
    assert find_sensitive_paths([bundle.name], root=tmp_path) == [bundle.name]


def test_ordinary_source_archive_is_not_treated_as_a_session(tmp_path):
    bundle = tmp_path / "source.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("README.md", "safe")
    assert archive_contains_sensitive_payload(bundle) is False


def test_shareable_payload_uses_the_same_sensitive_suffixes_as_repo_guard():
    payload = {"notes": ["local/model.sdf", "local/density.map"]}
    violations = public_payload_violations(payload)
    assert len(violations) == 2
    assert all("molecular/session filename" in item for item in violations)
