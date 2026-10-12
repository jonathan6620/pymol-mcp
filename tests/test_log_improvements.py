"""Regressions distilled from local histories; all examples are synthetic."""

import json
import sys
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


# October 2026 review -------------------------------------------------------


def _history(tmp_path):
    text = (tmp_path / "history.jsonl").read_text()
    return [json.loads(line) for line in text.splitlines() if line]


def _history_plugin(monkeypatch, tmp_path, name):
    monkeypatch.setenv("PYMOL_MCP_HISTORY", str(tmp_path))
    return load_plugin(name)


def _tool_wrappers():
    """The functions registered as MCP tools, by name.

    `mcp` is a stub here, so the decorated names are mocks; the stub still
    remembers what it was asked to register.
    """
    calls = server.mcp.tool.return_value.call_args_list
    return {call.args[0].__name__: call.args[0] for call in calls}


@pytest.mark.parametrize("text, value", [
    ("set label_position, (3,3,0), model and name CA", "(3,3,0)"),
    ("set label_position, [3, 3, 0], model and name CA", "[3, 3, 0]"),
])
def test_bracketed_setting_vector_is_one_value(text, value):
    parsed = server.parse_pymol_input(text)
    assert parsed.args == {
        "setting": "label_position", "value": value,
        "selection": "model and name CA",
    }


def test_scalar_setting_still_splits_at_the_comma():
    parsed = server.parse_pymol_input("set cartoon_transparency, 0.5, model")
    assert parsed.args["value"] == "0.5"
    assert parsed.args["selection"] == "model"


def test_plain_set_passes_a_vector_as_numbers():
    cmd = MagicMock()
    dispatch = load_plugin("vector_set").build_command_dispatcher(cmd)
    dispatch["set"]({"setting": "label_position", "value": "(3, -1.5,0)",
                     "selection": "model"})
    cmd.set.assert_called_once_with("label_position", [3.0, -1.5, 0.0], "model")


@pytest.mark.parametrize("value", ["(3,3)", "(3,3,0", "(a,b,c)", "(1,2,nan)",
                                   "(__import__('os'),0,0)"])
def test_plain_set_rejects_a_malformed_vector(value):
    cmd = MagicMock()
    dispatch = load_plugin("bad_vector_set").build_command_dispatcher(cmd)
    with pytest.raises(ValueError, match="three finite numbers"):
        dispatch["set"]({"setting": "label_position", "value": value})
    cmd.set.assert_not_called()


def test_unknown_setting_names_the_nearest(monkeypatch):
    fake = MagicMock()
    fake.setting.get_name_list.return_value = ["stick_radius", "stick_ball"]
    monkeypatch.setitem(sys.modules, "pymol", fake)
    cmd = MagicMock()
    dispatch = load_plugin("setting_hint").build_command_dispatcher(cmd)
    with pytest.raises(ValueError, match="did you mean stick_radius"):
        dispatch["inspect_setting"]({"name": "stick_radiu"})
    cmd.iterate.assert_not_called()


def _connection(monkeypatch, reply):
    """A real connection object whose socket round trip is captured."""
    connection = server.PyMOLConnection(port=9876)
    connection.sock = MagicMock()
    sent = []

    def round_trip(payload, timeout):
        sent.append(payload)
        return reply(payload) if callable(reply) else dict(reply)

    monkeypatch.setattr(connection, "_round_trip", round_trip)
    monkeypatch.setattr(server, "get_pymol_connection", lambda port=None: connection)
    return connection, sent


def test_command_unknown_to_the_plugin_reports_a_stale_plugin(monkeypatch):
    _connection(monkeypatch, {
        "status": "error", "message": "Unknown command: inspect_movie",
    })
    with pytest.raises(RuntimeError, match="older than this server"):
        server._introspect("inspect_movie", {}, 9876)
    output, ok, _ = server._execute_user_command(None, "show cartoon", 9876)
    assert not ok
    assert "older than this server" in output
    assert "SYNTAX_ERROR" not in output


def test_parser_rejection_is_recorded(monkeypatch):
    _, sent = _connection(monkeypatch, {"status": "success", "result": "ok"})
    output, ok, _ = server._execute_user_command(None, "please show it", 9876)
    assert not ok
    assert sent == [{
        "type": "history_event", "event": "rejected", "ok": False, "args": {},
        "detail": sent[0]["detail"], "source": "please show it",
    }]
    assert "not a known command" in sent[0]["detail"]


def test_rejection_recording_never_changes_the_result(monkeypatch):
    def unavailable(port=None):
        raise RuntimeError("No PyMOL is listening.")

    monkeypatch.setattr(server, "get_pymol_connection", unavailable)
    output, ok, _ = server._execute_user_command(None, "please show it")
    assert not ok
    assert output.startswith("No recognized PyMOL command")


def test_tool_call_tags_its_commands_and_records_its_rejections(monkeypatch):
    view = json.dumps([0.0] * 18)
    _, sent = _connection(monkeypatch, {
        "status": "success", "result": {"executed": True, "output": view},
    })
    tools = _tool_wrappers()
    tools["get_view"](ctx=None, instance=9876)
    tools["get_view"](ctx=None, instance=9876)
    first, second = sent
    assert first["tool"] == second["tool"] == "get_view"
    assert len(first["call_id"]) == 12
    assert first["call_id"] != second["call_id"]
    assert server._current_call.get() is None

    sent.clear()
    with pytest.raises(ValueError):
        tools["set_setting"](ctx=None, name="not a name", value=1,
                             scope="global", instance=9876)
    (event,) = sent
    assert event["event"] == "rejected"
    assert event["tool"] == "set_setting"
    assert event["args"] == {"name": "not a name", "value": 1,
                             "scope": "global", "instance": 9876}
    assert event["detail"] == "invalid setting name"


def test_history_records_duration_and_call(tmp_path, monkeypatch):
    plugin = _history_plugin(monkeypatch, tmp_path, "history_call")
    listener = plugin.SocketServer(port=9876)
    listener.command_callback = lambda name, args: {"executed": True}
    listener._handle_command({
        "type": "structured_command", "command": "count", "args": {},
        "source": "typed count", "call_id": "abc123", "tool": "count",
    })
    (record,) = _history(tmp_path)
    assert record["call_id"] == "abc123"
    assert record["tool"] == "count"
    assert record["duration_ms"] >= 0


def test_failed_command_is_not_replayable(tmp_path, monkeypatch):
    plugin = _history_plugin(monkeypatch, tmp_path, "history_failed")
    plugin._record_history(
        "set", {"setting": "label_size", "value": "x"}, "set label_size, x",
        {"executed": False, "error": "bad value"}, replay="set label_size, x",
    )
    (record,) = _history(tmp_path)
    assert record["ok"] is False
    assert record["replayable"] is False


def test_session_script_names_the_plugin_that_wrote_it(tmp_path, monkeypatch):
    plugin = _history_plugin(monkeypatch, tmp_path, "history_fingerprint")
    plugin._record_history("count", {}, "typed count", {"executed": True})
    (script,) = tmp_path.glob("session-*.pml")
    assert f"# Written by: plugin {plugin.PLUGIN_FINGERPRINT}" in script.read_text()
    assert len(plugin.PLUGIN_FINGERPRINT) == 12


def test_plugin_records_server_events(tmp_path, monkeypatch):
    plugin = _history_plugin(monkeypatch, tmp_path, "history_events")
    listener = plugin.SocketServer(port=9876)
    target = tmp_path / "spin.gif"
    assert listener._handle_command({
        "type": "history_event", "event": "rejected", "detail": "no match",
        "source": "please show it", "call_id": "abc123",
        "tool": "parse_and_execute",
    })["executed"]
    assert listener._handle_command({
        "type": "history_event", "event": "render_movie", "ok": True,
        "args": {"frames": 24}, "detail": "Rendered 24 frames",
        "source": "typed render_movie", "path": str(target),
    })["executed"]
    rejected, movie = _history(tmp_path)
    assert rejected["event"] == "rejected"
    assert rejected["ok"] is False
    assert rejected["detail"] == "no match"
    assert rejected["source"] == "please show it"
    assert rejected["tool"] == "parse_and_execute"
    assert movie["command"] == "render_movie"
    assert movie["ok"] is True
    assert movie["output"] == "Rendered 24 frames"
    assert movie["file"] == {"path": str(target), "direction": "out"}
    # Neither is PyMOL syntax, so neither may reach the replay script.
    (script,) = tmp_path.glob("session-*.pml")
    assert "render_movie" not in script.read_text()
    assert "please" not in script.read_text()


@pytest.mark.parametrize("message", [
    {"event": "anything_else"},
    {"event": "rejected", "args": "not an object"},
    {"event": "rejected", "detail": 5},
])
def test_plugin_refuses_malformed_server_events(tmp_path, monkeypatch, message):
    plugin = _history_plugin(monkeypatch, tmp_path, "history_bad_events")
    listener = plugin.SocketServer(port=9876)
    result = listener._handle_command({"type": "history_event", **message})
    assert result["executed"] is False
    assert not (tmp_path / "history.jsonl").exists()

