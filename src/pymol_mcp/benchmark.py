"""Local replay benchmarks without copying molecular inputs into the repository."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

SAFE_REPLAY_COMMANDS = frozenset(
    {
        "as",
        "bg_color",
        "cartoon",
        "center",
        "color",
        "create",
        "delete",
        "deselect",
        "disable",
        "distance",
        "enable",
        "fetch",
        "fragment",
        "frame",
        "hide",
        "load",
        "orient",
        "reinitialize",
        "remove",
        "select",
        "set",
        "set_view",
        "show",
        "spectrum",
        "turn",
        "unset",
        "viewport",
        "zoom",
    }
)
OUTPUT_COMMANDS = frozenset({"png", "save"})
PYMOL_ERROR_MARKERS = (
    "Error:",
    "Error-fetch:",
    "Selector-Error:",
    "CmdException:",
    "Traceback (most recent call last):",
)
MOLECULAR_SUFFIXES = frozenset(
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


def load_scenario(path: Path) -> dict[str, Any]:
    scenario = json.loads(path.read_text())
    if scenario.get("schema_version") != 1:
        raise ValueError("unsupported benchmark schema_version")
    if not scenario.get("id") or not scenario.get("semantic"):
        raise ValueError("scenario requires id and semantic sections")
    return scenario


def inspect_bundle(bundle: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    """Inspect names only; molecular payloads must never be extracted."""
    expected = sorted(scenario["privacy"]["archive_entries"])
    with zipfile.ZipFile(bundle) as archive:
        names = sorted(archive.namelist())
        embedded = [
            name
            for name in names
            if Path(name).suffix.lower() in MOLECULAR_SUFFIXES
        ]
        unsafe = [
            name
            for name in names
            if Path(name).is_absolute() or ".." in Path(name).parts
        ]
        try:
            history = [
                json.loads(line)
                for line in archive.read("history.jsonl").decode().splitlines()
                if line
            ]
            valid_history = all(isinstance(item, dict) for item in history)
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
            history = []
            valid_history = False
    checks = {
        "expected_entries": names == expected,
        "no_embedded_molecular_files": not embedded,
        "safe_archive_names": not unsafe,
        "valid_history_jsonl": valid_history,
    }
    metrics = {
        "history_entries": len(history),
        "failed_entries": sum(item.get("ok") is False for item in history),
        "replayable_entries": sum(bool(item.get("replayable")) for item in history),
        "render_iterations": sum(item.get("command") == "png" for item in history),
        "camera_changes": sum(
            item.get("command") in {"orient", "set_view", "turn", "zoom"}
            for item in history
        ),
    }
    return {"checks": checks, "metrics": metrics, "passed": all(checks.values())}


def cleaned_replay(text: str) -> str:
    """Remove artifact writes and reject commands outside the MCP replay surface."""
    kept = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ";" in line or "\x00" in line:
            raise ValueError(f"unsafe replay syntax on line {number}")
        command = line.split(None, 1)[0].lower()
        if command in OUTPUT_COMMANDS:
            continue
        if command not in SAFE_REPLAY_COMMANDS:
            raise ValueError(f"unsafe replay command on line {number}: {command}")
        kept.append(line)
    if not kept or kept[0] != "reinitialize":
        raise ValueError("replay must begin with reinitialize")
    return "\n".join(kept) + "\n"


def pymol_error_lines(output: str) -> list[str]:
    """Find errors PyMOL prints while still returning process status zero."""
    return [
        line.strip()
        for line in output.splitlines()
        if any(line.strip().startswith(marker) for marker in PYMOL_ERROR_MARKERS)
    ]


def find_pymol(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit if os.access(explicit, os.X_OK) else None
    found = shutil.which("pymol")
    if found:
        return found
    patterns = (
        "~/*conda*/envs/*/bin/pymol",
        "/opt/*conda*/envs/*/bin/pymol",
        "/opt/homebrew/Caskroom/*/base/envs/*/bin/pymol",
        "/usr/local/*conda*/envs/*/bin/pymol",
        "/Applications/PyMOL.app/Contents/bin/pymol",
    )
    import glob

    for pattern in patterns:
        for hit in glob.glob(os.path.expanduser(pattern)):
            if os.access(hit, os.X_OK):
                return hit
    return None


def _verification_script(
    session: Path, image: Path, scenario: dict[str, Any]
) -> str:
    semantic = scenario["semantic"]
    selections = {
        item["id"]: item["expression"] for item in semantic["selections"]
    }
    render = scenario["image"]
    return (
        "import json\n"
        "from pymol import cmd\n"
        f"cmd.load({str(session)!r})\n"
        f"expressions = {selections!r}\n"
        "state = {\n"
        "  'objects': {name: cmd.count_atoms(name) for name in "
        "cmd.get_object_list('all')},\n"
        "  'named_selections': sorted(cmd.get_names('selections')),\n"
        "  'selections': {name: cmd.count_atoms(expr) for name, expr in "
        "expressions.items()},\n"
        "  'view': list(cmd.get_view()),\n"
        "  'version': list(cmd.get_version()),\n"
        "}\n"
        "print('PYMOL_BENCHMARK_STATE=' + json.dumps(state, sort_keys=True))\n"
        f"cmd.png({str(image)!r}, width={render['width']}, "
        f"height={render['height']}, dpi={render['dpi']}, "
        f"ray={1 if render['ray'] else 0}, quiet=1)\n"
    )


def _read_state(stdout: str) -> dict[str, Any]:
    prefix = "PYMOL_BENCHMARK_STATE="
    lines = [
        line[line.index(prefix) :]
        for line in stdout.splitlines()
        if prefix in line
    ]
    if len(lines) != 1:
        raise RuntimeError(
            "fresh PyMOL did not emit one benchmark state record:\n" + stdout
        )
    return json.loads(lines[0][len(prefix) :])


def _image_metrics(path: Path, reference: Path | None = None) -> dict[str, Any]:
    image = Image.open(path).convert("RGBA")
    alpha = image.getchannel("A")
    result: dict[str, Any] = {
        "width": image.width,
        "height": image.height,
        "opaque": alpha.getextrema() == (255, 255),
        "white_corner": image.getpixel((0, 0)) == (255, 255, 255, 255),
    }
    if reference:
        expected = Image.open(reference).convert("RGB")
        actual = image.convert("RGB")
        if expected.size != actual.size:
            expected = expected.resize(actual.size, Image.Resampling.LANCZOS)
        diff = ImageChops.difference(actual, expected)
        stat = ImageStat.Stat(diff)
        result["reference_mean_absolute_error"] = sum(stat.mean) / 3
        result["reference_rms"] = math.sqrt(sum(value**2 for value in stat.rms) / 3)
    return result


def score(
    scenario: dict[str, Any],
    state: dict[str, Any],
    image: dict[str, Any],
    privacy: dict[str, Any],
) -> dict[str, Any]:
    semantic = scenario["semantic"]
    render = scenario["image"]
    expected_view = semantic["view_rotation"]
    actual_view = state["view"][:9]
    view_error = max(abs(a - b) for a, b in zip(actual_view, expected_view))
    checks = {
        "objects": state["objects"] == semantic["objects"],
        "selections": state["selections"]
        == {item["id"]: item["atoms"] for item in semantic["selections"]},
        "named_selections": all(
            name in state["named_selections"]
            for name in semantic["named_selections"]
        ),
        "published_side_camera": view_error
        <= semantic["view_rotation_max_error"],
        "image_dimensions": (image["width"], image["height"])
        == (render["width"], render["height"]),
        "opaque_white_background": image["opaque"] and image["white_corner"],
        "bundle_privacy": privacy["passed"],
    }
    if "reference_mean_absolute_error" in image:
        checks["reference_similarity"] = (
            image["reference_mean_absolute_error"]
            <= render["reference_max_mean_absolute_error"]
        )
    return {
        "schema_version": 1,
        "scenario": scenario["id"],
        "passed": all(checks.values()),
        "score": sum(checks.values()) / len(checks),
        "checks": checks,
        "metrics": {
            "view_rotation_max_error": view_error,
            "trajectory": privacy.get("metrics", {}),
            **image,
        },
        "runtime": {"pymol_version": state["version"][0]},
    }


def evaluate(
    bundle: Path,
    scenario_path: Path,
    pymol: str,
    reference: Path | None = None,
) -> dict[str, Any]:
    scenario = load_scenario(scenario_path)
    privacy = inspect_bundle(bundle, scenario)
    with zipfile.ZipFile(bundle) as archive:
        replay_text = archive.read("replay.pml").decode()
    with tempfile.TemporaryDirectory(prefix="pymol-benchmark-") as raw:
        workdir = Path(raw)
        replay = workdir / "replay.pml"
        session = workdir / "candidate.pse"
        image = workdir / "candidate.png"
        replay.write_text(cleaned_replay(replay_text) + f"save {session}\n")
        replay_proc = subprocess.run(
            [pymol, "-cq", str(replay)],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        replay_output = replay_proc.stdout + "\n" + replay_proc.stderr
        replay_errors = pymol_error_lines(replay_output)
        if replay_proc.returncode != 0 or not session.exists() or replay_errors:
            raise RuntimeError(
                "PyMOL replay failed:\n"
                + replay_output
            )
        verify = workdir / "verify.py"
        verify.write_text(_verification_script(session, image, scenario))
        proc = subprocess.run(
            [pymol, "-cq", str(verify)],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"PyMOL replay failed:\n{proc.stdout}\n{proc.stderr}")
        verify_output = proc.stdout + "\n" + proc.stderr
        verify_errors = pymol_error_lines(verify_output)
        if verify_errors:
            raise RuntimeError("PyMOL verification failed:\n" + verify_output)
        state = _read_state(verify_output)
        metrics = _image_metrics(image, reference)
    return score(scenario, state, metrics, privacy)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument(
        "--scenario",
        type=Path,
        default=Path("benchmarks/scenarios/barhl2_figure2b.json"),
    )
    parser.add_argument("--pymol")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    pymol = find_pymol(args.pymol)
    if not pymol:
        parser.error("could not find PyMOL; pass --pymol /absolute/path/to/pymol")
    result = evaluate(args.bundle, args.scenario, pymol, args.reference)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
