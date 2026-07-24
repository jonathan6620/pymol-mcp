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
