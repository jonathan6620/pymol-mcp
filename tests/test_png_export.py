import pytest
from conftest import load_plugin


@pytest.fixture(scope="module")
def plugin():
    return load_plugin("plugin_png_export")


class FakeCmd:
    def __init__(self):
        self.calls = []

    def png(self, filename, **options):
        self.calls.append((filename, options))
        return 1

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def test_png_keywords_reach_cmd_png(plugin):
    fake = FakeCmd()
    dispatch = plugin.build_command_dispatcher(fake)
    dispatch["png"]({
        "filename": "/tmp/figure.png",
        "options": "width=2400, height=2000, dpi=300, ray=1, quiet=0",
    })
    assert fake.calls == [
        (
            "/tmp/figure.png",
            {"width": 2400, "height": 2000, "dpi": 300.0, "ray": 1, "quiet": 0},
        )
    ]


def test_png_positional_options_are_supported(plugin):
    assert plugin._parse_png_options("2400, 2000, 300, 1, 0") == {
        "width": 2400,
        "height": 2000,
        "dpi": 300.0,
        "ray": 1,
        "quiet": 0,
    }


def test_typed_png_options_are_validated_at_socket_boundary(plugin):
    fake = FakeCmd()
    dispatch = plugin.build_command_dispatcher(fake)
    dispatch["png"]({
        "filename": "/tmp/figure.png",
        "width": 2400,
        "height": 2000,
        "dpi": 300,
        "ray": 1,
        "quiet": 1,
    })
    assert fake.calls[0][1] == {
        "width": 2400,
        "height": 2000,
        "dpi": 300.0,
        "ray": 1,
        "quiet": 1,
    }


def test_typed_and_text_png_options_cannot_be_mixed(plugin):
    fake = FakeCmd()
    dispatch = plugin.build_command_dispatcher(fake)
    with pytest.raises(ValueError, match="either typed"):
        dispatch["png"]({"width": 10, "options": "height=10"})
    assert fake.calls == []


@pytest.mark.parametrize(
    "options",
    [
        "width=2400, width=1",
        "renderer=evil",
        "width=-1",
        "ray=2",
        "quiet=true",
        "width=10001",
        "width=10000, height=10000",
        "dpi=0",
        "dpi=2401",
        "dpi=nan",
        "width=2400\nrun /tmp/evil.pml",
        "2400,,2000",
    ],
)
def test_invalid_png_options_are_rejected(plugin, options):
    with pytest.raises(ValueError):
        plugin._parse_png_options(options)
