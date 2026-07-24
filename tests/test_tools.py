import struct
import zlib
from pathlib import Path

import pytest

import pymol_mcp.server as server


def _chunk(kind: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(kind)
    checksum = zlib.crc32(data, checksum) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)


def _minimal_png(path: Path, width: int, height: int):
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", b"")
        + _chunk(b"IEND", b"")
    )


def test_execute_batch_runs_in_order(monkeypatch):
    seen = []

    marker = object()

    def fake_execute(ctx, command, instance, connection):
        seen.append((command, instance))
        assert connection is None if command == "show cartoon" else connection is marker
        return "ok", True, marker

    monkeypatch.setattr(server, "_execute_user_command", fake_execute)
    output = server._execute_batch(None, ["show cartoon", "zoom visible"], 9876)
    assert seen == [("show cartoon", 9876), ("zoom visible", 9876)]
    assert "1. 'show cartoon': ok" in output
    assert "2. 'zoom visible': ok" in output


def test_execute_batch_stops_after_failure(monkeypatch):
    def fake_execute(ctx, command, instance, connection):
        if command == "bad":
            return "diagnostic wording may change", False, connection
        return "ok", True, connection

    monkeypatch.setattr(server, "_execute_user_command", fake_execute)
    output = server._execute_batch(
        None, ["good", "bad", "never"], stop_on_error=True
    )
    assert "Stopped after command 2." in output
    assert "never" not in output


def test_execute_batch_can_continue_after_failure(monkeypatch):
    monkeypatch.setattr(
        server,
        "_execute_user_command",
        lambda ctx, command, instance, connection: (
            "diagnostic" if command == "bad" else "ok",
            command != "bad",
            connection,
        ),
    )
    output = server._execute_batch(
        None, ["bad", "still-runs"], stop_on_error=False
    )
    assert "still-runs" in output


def test_render_png_returns_verified_image(monkeypatch, tmp_path):
    class FakeConnection:
        def send_command(self, command, args, source=None, timeout=None):
            assert command == "png"
            _minimal_png(Path(args["filename"]), args["width"], args["height"])
            return {
                "status": "success",
                "result": {"executed": True, "output": "1"},
            }

    monkeypatch.setattr(
        server, "get_pymol_connection", lambda instance=None: FakeConnection()
    )
    output_path = tmp_path / "render.png"
    meta = server._render_png(
        None, str(output_path), width=640, height=480, dpi=300
    )
    assert (meta.width, meta.height) == (640, 480)
    assert meta.dpi == 300
    assert meta.ray is True
    assert meta.path == str(output_path)
    assert output_path.exists()


def test_render_failure_does_not_accept_or_replace_stale_file(monkeypatch, tmp_path):
    class FailedConnection:
        def send_command(self, command, args, source=None, timeout=None):
            return {
                "status": "success",
                "result": {"executed": True, "output": "0"},
            }

    monkeypatch.setattr(
        server, "get_pymol_connection", lambda instance=None: FailedConnection()
    )
    output_path = tmp_path / "render.png"
    _minimal_png(output_path, 640, 480)
    original = output_path.read_bytes()
    with pytest.raises(RuntimeError, match="reported.*failed"):
        server._render_png(
            None, str(output_path), width=640, height=480, dpi=300
        )
    assert output_path.read_bytes() == original


@pytest.mark.parametrize(
    ("filename", "width", "height", "dpi"),
    [
        ("out.jpg", 640, 480, 300),
        ("out.png", 0, 480, 300),
        ("out.png", 20_000, 480, 300),
        ("out.png", 10_000, 10_000, 300),
        ("out.png", 640, 480, 0),
    ],
)
def test_render_png_rejects_invalid_requests(filename, width, height, dpi):
    with pytest.raises(ValueError):
        server._render_png(
            None, filename, width=width, height=height, dpi=dpi
        )


def test_png_validation_rejects_truncated_file(tmp_path):
    path = tmp_path / "truncated.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(RuntimeError, match="truncated"):
        server._png_dimensions(path)


##############################################################################
# render_movie
##############################################################################


class _FakeMovieConnection:
    """Records the commands sent and writes a decodable PNG for each frame.

    Unlike _minimal_png, which has an empty IDAT and only satisfies the header
    parser, these frames have to survive being reopened and re-encoded by
    Pillow. Each frame gets a different colour so an encoder that silently
    collapses identical frames would show up.
    """

    def __init__(self):
        self.commands = []
        self._frame = 0

    def send_command(self, command, args, source=None, timeout=None):
        self.commands.append((command, args))
        if command == "png":
            from PIL import Image as PILImage

            shade = (self._frame * 37) % 256
            PILImage.new(
                "RGB", (args["width"], args["height"]), (shade, 80, 255 - shade)
            ).save(args["filename"])
            self._frame += 1
        return {"status": "success", "result": {"executed": True, "output": "1"}}


def _movie(monkeypatch, tmp_path, **kwargs):
    conn = _FakeMovieConnection()
    monkeypatch.setattr(server, "get_pymol_connection", lambda instance=None: conn)
    kwargs.setdefault("filename", str(tmp_path / "movie.gif"))
    kwargs.setdefault("frames", 4)
    kwargs.setdefault("width", 64)
    kwargs.setdefault("height", 48)
    meta, path = server._render_movie(None, **kwargs)
    return meta, path, conn


def test_render_movie_spins_a_full_turn(monkeypatch, tmp_path):
    meta, path, conn = _movie(monkeypatch, tmp_path, frames=4, mode="spin")
    turns = [a for c, a in conn.commands if c == "turn"]
    # The first frame is the current view, so a 4-frame spin turns three times.
    assert len(turns) == 3
    assert all(a["angle"] == 90.0 for a in turns)
    assert meta.frames == 4
    assert meta.mode == "spin"
    assert path.exists()


def test_render_movie_steps_states(monkeypatch, tmp_path):
    _, _, conn = _movie(
        monkeypatch, tmp_path, frames=3, mode="states", start_state=5
    )
    frames = [a["frame_number"] for c, a in conn.commands if c == "frame"]
    assert frames == [5, 6, 7]


def test_render_movie_output_is_a_real_animation(monkeypatch, tmp_path):
    """A successful save is not proof of content -- reopen and count."""
    from PIL import Image as PILImage

    meta, path, _ = _movie(monkeypatch, tmp_path, frames=5)
    with PILImage.open(path) as check:
        assert check.format == "GIF"
        assert check.n_frames == 5
    assert meta.bytes == path.stat().st_size


def test_render_movie_caps_frame_count_and_says_so(monkeypatch, tmp_path):
    meta, _, _ = _movie(monkeypatch, tmp_path, frames=500)
    assert meta.frames == server.MOVIE_MAX_FRAMES
    assert meta.truncated is True
    assert meta.dropped_frames == 500 - server.MOVIE_MAX_FRAMES
    assert "capped" in (meta.note or "")


def test_render_movie_downscales_over_pixel_cap(monkeypatch, tmp_path):
    meta, _, _ = _movie(monkeypatch, tmp_path, frames=2, width=4000, height=4000)
    assert meta.width * meta.height <= server.MOVIE_MAX_PIXELS
    assert meta.truncated is True
    assert "downscaled" in (meta.note or "")


def test_render_movie_webp_accepted(monkeypatch, tmp_path):
    meta, path, _ = _movie(
        monkeypatch, tmp_path, filename=str(tmp_path / "m.webp"), frames=3
    )
    assert path.suffix == ".webp"
    assert meta.frames == 3


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"mode": "wobble"}, "mode must be"),
        ({"axis": "q"}, "axis must be"),
        ({"frames": 1}, "at least 2 frames"),
        ({"fps": 0}, "fps must be"),
        ({"width": 0}, "between 1 and 4000"),
    ],
)
def test_render_movie_rejects_bad_requests(monkeypatch, tmp_path, kwargs, message):
    with pytest.raises(ValueError, match=message):
        _movie(monkeypatch, tmp_path, **kwargs)


def test_render_movie_rejects_non_animation_suffix(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="gif or .webp"):
        _movie(monkeypatch, tmp_path, filename=str(tmp_path / "m.png"))


def test_render_movie_cleans_up_frame_files(monkeypatch, tmp_path):
    _movie(monkeypatch, tmp_path, frames=3)
    leftovers = [p for p in tmp_path.rglob("*.png")]
    assert leftovers == [], f"temporary frames left behind: {leftovers}"


##############################################################################
# typed effect commands
##############################################################################


class _RecordingConnection:
    def __init__(self, result=None):
        self.sent = []
        self._result = result or {"executed": True, "output": "ok"}

    def send_command(self, command, args, source=None, timeout=None):
        self.sent.append((command, args))
        if command == "count":
            return {
                "status": "success",
                "result": {
                    "executed": True,
                    "data": {
                        "selection": args["selection"],
                        "atoms": 104,
                        "residues": 5,
                        "chains": 1,
                    },
                },
            }
        return {"status": "success", "result": self._result}


def _conn(monkeypatch, result=None):
    conn = _RecordingConnection(result)
    monkeypatch.setattr(server, "get_pymol_connection", lambda instance=None: conn)
    return conn


def test_apply_renders_the_selector_and_escapes_negatives(monkeypatch):
    from pymol_mcp.api import ResidueRange, Selector

    conn = _conn(monkeypatch)
    server._apply(
        None,
        "color",
        Selector(chain="E", residue_range=ResidueRange(start=-12, end=-8)),
        value="red",
    )
    command, args = conn.sent[0]
    assert command == "color"
    assert args["color"] == "red"
    # The caller wrote integers; the escaping is the facade's job.
    assert args["selection"] == r"(chain E) and (resi \-12-\-8)"


def test_apply_uses_the_right_argument_name_per_command(monkeypatch):
    from pymol_mcp.api import Selector

    for command, key, value in [
        ("show", "representation", "cartoon"),
        ("cartoon", "type", "tube"),
        ("as", "representation", "sticks"),
    ]:
        conn = _conn(monkeypatch)
        server._apply(None, command, Selector(chain="A"), value=value)
        assert conn.sent[0][1][key] == value


def test_apply_selection_only_commands_take_no_value(monkeypatch):
    from pymol_mcp.api import Selector

    conn = _conn(monkeypatch)
    server._apply(None, "zoom", Selector(object="bac"))
    assert conn.sent[0] == ("zoom", {"selection": "bac"})


def test_apply_object_commands_use_name_not_selection(monkeypatch):
    from pymol_mcp.api import Selector

    conn = _conn(monkeypatch)
    server._apply(None, "disable", Selector(object="bac"))
    assert conn.sent[0] == ("disable", {"name": "bac"})


def test_apply_requires_a_value_where_one_is_needed(monkeypatch):
    from pymol_mcp.api import Selector

    _conn(monkeypatch)
    with pytest.raises(ValueError, match="needs a value"):
        server._apply(None, "color", Selector(chain="A"))


def test_apply_rejects_commands_that_are_not_effects(monkeypatch):
    from pymol_mcp.api import Selector

    _conn(monkeypatch)
    with pytest.raises(ValueError, match="not an effect command"):
        server._apply(None, "ray", Selector(chain="A"))


def test_select_returns_typed_counts(monkeypatch):
    from pymol_mcp.api import ResidueRange, Selector

    conn = _conn(monkeypatch)
    counts = server._select(
        None, "ibs2", Selector(chain="E", residue_range=ResidueRange(start=-12, end=-8))
    )
    assert [c for c, _ in conn.sent] == ["select", "count"]
    assert counts.atoms == 104
    assert counts.residues == 5
