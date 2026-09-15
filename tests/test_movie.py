"""Movie plans, timing, state restoration, and protocol boundary validation."""

from unittest.mock import MagicMock

import pytest
from conftest import load_plugin
from PIL import Image
from test_tools import _FakeMovieConnection, _movie

from pymol_mcp import server
from pymol_mcp.api import MovieSegment


def test_timeline_ranges_pauses_and_reversal():
    segments = [MovieSegment(start=1, end=5, stride=2), MovieSegment(start=5, hold=3),
                MovieSegment(start=3, end=1)]
    assert server._movie_states(segments, False) == [1, 3, 5, 5, 5, 5, 3, 2, 1]
    assert server._movie_states([MovieSegment(start=1, end=3)], True) == [1, 2, 3, 2]


def test_timeline_size_is_bounded_before_expansion():
    with pytest.raises(ValueError, match="10000"):
        server._movie_states([MovieSegment(start=1, end=10000, hold=10000)], False)
    with pytest.raises(ValueError, match="10000"):
        server._movie_states([MovieSegment(start=1, end=6000)], True)


@pytest.mark.parametrize("args", [
    {"states": []}, {"states": [0]}, {"states": [True]}, {"states": [1], "fps": 0},
    {"states": [1], "loop": "yes"}, {"states": [11]}, {"states": [1] * 10001},
])
def test_invalid_timeline_does_not_change_scene(args):
    cmd = MagicMock()
    cmd.count_states.return_value = 10
    dispatch = load_plugin("movie_validation").build_command_dispatcher(cmd)
    with pytest.raises(ValueError):
        dispatch["configure_movie"](args)
    cmd.mstop.assert_not_called()
    cmd.mset.assert_not_called()


def test_timeline_mode_seeks_and_restores_frame(monkeypatch, tmp_path):
    _, _, conn = _movie(monkeypatch, tmp_path, mode="timeline", start_frame=3, frames=3)
    frames = [args for command, args in conn.commands if command == "frame"]
    assert [args["frame_number"] for args in frames] == [3, 4, 5, 1]
    assert all(args["trigger"] == 0 for args in frames)
    assert conn.commands[-1] == ("set_view", {"view": list(range(18))})


def test_render_failure_restores_playback_and_preserves_output(monkeypatch, tmp_path):
    class FailingConnection(_FakeMovieConnection):
        def send_command(self, command, args, **kwargs):
            response = super().send_command(command, args, **kwargs)
            if command == "inspect_movie":
                response["result"]["data"]["playing"] = True
            if command == "png":
                return {"status": "error", "message": "synthetic render failure"}
            return response

    conn = FailingConnection()
    monkeypatch.setattr(server, "get_pymol_connection", lambda instance: conn)
    output = tmp_path / "keep.gif"
    output.write_bytes(b"previous output")
    with pytest.raises(RuntimeError, match="synthetic render failure"):
        server._render_movie(None, str(output), mode="timeline", frames=2)
    assert conn.commands[-1] == ("mplay", {})
    assert ("frame", {"frame_number": 1, "trigger": 0}) in conn.commands
    assert ("set_view", {"view": list(range(18))}) in conn.commands
    assert output.read_bytes() == b"previous output"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep.gif"]


def test_thinning_keeps_full_duration_and_endpoints(monkeypatch, tmp_path):
    original = server._encode_animation
    attempts = []

    def encode(paths, destination, fps, fmt, durations):
        attempts.append(([p.name for p in paths], durations))
        size = original(paths, destination, fps, fmt, durations)
        return server.MOVIE_MAX_BYTES + 1 if len(paths) > 3 else size

    monkeypatch.setattr(server, "_encode_animation", encode)
    meta, path, _ = _movie(monkeypatch, tmp_path, frames=8, fps=10)
    assert meta.frames == 2
    assert meta.dropped_frames == 6
    assert meta.duration_ms == 800
    assert meta.effective_fps == 2.5
    assert attempts[-1][0] == ["f00000.png", "f00007.png"]
    assert all(sum(durations) == 800 for _, durations in attempts)
    with Image.open(path) as image:
        assert image.info["duration"] == 700
        image.seek(1)
        assert image.info["duration"] == 100


def test_initial_frame_cap_keeps_full_duration(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "MOVIE_MAX_FRAMES", 4)
    meta, _, _ = _movie(monkeypatch, tmp_path, frames=100, fps=10)
    assert meta.duration_ms == 10000
    assert meta.rendered_frames == 4
    assert meta.dropped_frames == 96


def test_identical_frames_can_merge_without_losing_pauses(tmp_path):
    paths = []
    for index in range(3):
        path = tmp_path / f"{index}.png"
        Image.new("RGB", (8, 8), "red").save(path)
        paths.append(path)
    output = tmp_path / "pause.gif"
    server._encode_animation(paths, output, 10, "GIF", [100, 200, 300])
    assert server._verify_animation(output, 3, 600) == (1, 600)


def test_duration_rounding_does_not_accumulate():
    durations = server._frame_durations(list(range(120)), 120, 24, "GIF")
    assert sum(durations) == 5000
    assert set(durations) <= {40, 50}
