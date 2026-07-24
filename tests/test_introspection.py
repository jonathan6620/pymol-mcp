import json
import struct
import zlib

import pytest
from conftest import load_plugin

from pymol_mcp.server import _command_timeout, _png_dimensions, parse_pymol_input


@pytest.fixture(scope="module")
def plugin():
    return load_plugin("plugin_introspection")


class FakeCmd:
    def __init__(self):
        self.view = tuple(float(index) for index in range(18))
        self.restored = None

    def get_view(self):
        return self.view

    def set_view(self, view):
        self.restored = view
        return 1

    def get(self, name):
        return {"ambient": 0.45}[name]

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def test_view_and_setting_commands_parse():
    assert parse_pymol_input("get_view").args == {}
    assert parse_pymol_input("get_setting ambient").args == {"name": "ambient"}
    view = json.dumps([float(index) for index in range(18)])
    assert parse_pymol_input(f"set_view {view}").args == {"view": view}


def test_view_round_trip_through_dispatcher(plugin):
    fake = FakeCmd()
    dispatch = plugin.build_command_dispatcher(fake)
    encoded = dispatch["get_view"]({})
    assert json.loads(encoded) == list(fake.view)
    dispatch["set_view"]({"view": encoded})
    assert fake.restored == fake.view


def test_get_setting_returns_json(plugin):
    fake = FakeCmd()
    dispatch = plugin.build_command_dispatcher(fake)
    assert json.loads(dispatch["get_setting"]({"name": "ambient"})) == {
        "name": "ambient",
        "value": 0.45,
    }


@pytest.mark.parametrize(
    "view",
    [
        [],
        [0] * 17,
        [0] * 17 + ["not-a-number"],
        [0] * 17 + [float("inf")],
        "__import__('os').system('id')",
    ],
)
def test_invalid_views_are_rejected(plugin, view):
    with pytest.raises(ValueError):
        plugin._parse_view(view)


@pytest.mark.parametrize("name", ["ambient\nrun evil.pml", "1ambient", "", "a-b"])
def test_invalid_setting_names_are_rejected(plugin, name):
    dispatch = plugin.build_command_dispatcher(FakeCmd())
    with pytest.raises(ValueError):
        dispatch["get_setting"]({"name": name})


def test_render_timeout_scales_with_pixels():
    normal = _command_timeout("show", {})
    small = _command_timeout("png", {"width": 1200, "height": 1200})
    large = _command_timeout("png", {"width": 4000, "height": 4000})
    assert normal == 10.0
    assert 300.0 <= small < large <= 1800.0


def test_png_dimensions_reads_ihdr(tmp_path):
    path = tmp_path / "image.png"
    ihdr = struct.pack(">IIBBBBB", 2400, 1800, 8, 6, 0, 0, 0)

    def chunk(kind, data):
        checksum = zlib.crc32(kind)
        checksum = zlib.crc32(data, checksum) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", b"")
        + chunk(b"IEND", b"")
    )
    assert _png_dimensions(path) == (2400, 1800)
