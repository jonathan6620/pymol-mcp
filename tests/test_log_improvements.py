"""Regressions distilled from local histories; all examples are synthetic."""

from unittest.mock import MagicMock

import pytest
from conftest import load_plugin

from pymol_mcp import server
from pymol_mcp.api import Selector


def test_test_plugins_do_not_use_personal_history():
    plugin = load_plugin("isolated_history")
    assert plugin._history_directory() is None


def test_cached_connection_needs_no_refresh(monkeypatch):
    connection = MagicMock()
    monkeypatch.setattr(server, "_connections", {9876: connection})
    assert server.get_pymol_connection(9876) is connection
    connection.send_command.assert_not_called()


def test_disconnected_cache_is_replaced(monkeypatch):
    old = MagicMock(sock=None)
    replacement = MagicMock()
    monkeypatch.setattr(server, "_connections", {9876: old})
    monkeypatch.setattr(server, "PyMOLConnection", lambda **kwargs: replacement)
    assert server.get_pymol_connection(9876) is replacement
    replacement.connect.assert_called_once_with()
    old.send_command.assert_not_called()


def test_spectrum_bounds_are_not_part_of_selection():
    parsed = server.parse_pymol_input("spectrum b, blue_red, model, -1, 2")
    assert parsed.args == {
        "expression": "b", "palette": "blue_red", "selection": "model",
        "minimum": "-1", "maximum": "2",
    }
    cmd = MagicMock()
    plugin = load_plugin("bounded_spectrum")
    plugin.build_command_dispatcher(cmd)["spectrum"](parsed.args)
    cmd.spectrum.assert_called_once_with(
        "b", "blue_red", "model", minimum=-1.0, maximum=2.0,
    )


@pytest.mark.parametrize("args", [
    {"minimum": "nan"}, {"maximum": "inf"},
    {"minimum": 2, "maximum": 1},
    {"expression": "__import__('os').system('id')"},
])
def test_spectrum_rejects_invalid_bounds_and_code(args):
    cmd = MagicMock()
    dispatch = load_plugin("invalid_spectrum").build_command_dispatcher(cmd)
    with pytest.raises(ValueError):
        dispatch["spectrum"](args)
    cmd.spectrum.assert_not_called()


@pytest.mark.parametrize("text", ["Site 1", "C1'", 'Say "hello"', "", "x\ny"])
def test_literal_labels_are_quoted_and_replayable(monkeypatch, text):
    import ast

    connection = MagicMock()
    connection.send_command.return_value = {
        "status": "success", "result": {"executed": True, "output": "ok"},
    }
    monkeypatch.setattr(server, "get_pymol_connection", lambda port: connection)
    server._label_text(Selector(chain="A", atom_names=["CA"]), text, 9876)
    call = connection.send_command.call_args
    command, args = call.args
    assert command == "label"
    assert ast.literal_eval(args["expression"]) == text
    plugin = load_plugin("literal_label")
    cmd = MagicMock()
    plugin.build_command_dispatcher(cmd)["label"](args)
    cmd.label.assert_called_once_with(args["selection"], repr(text))
    assert "\n" not in call.kwargs["replay"]
    assert plugin._replay_source(command, call.kwargs["replay"], args) is not None
