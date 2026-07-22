"""The listener must not die silently, and must be restartable when it does.

Reported after a live session: the listener stopped answering after two
successful commands while PyMOL stayed up. Three defects combined to make it
unrecoverable and invisible.

  1. One escaped exception ended the accept loop for the life of the process.
  2. Every diagnostic on that path went through _log, which is off by default,
     so nothing reached the client, the console, or history.jsonl.
  3. A separate `listening` flag was never reset, so start_socket_server()
     returned False without doing anything and the dialog showed green while
     nothing was bound.
"""

import json
import socket
import time

import pytest
from conftest import free_port, load_plugin


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("PYMOL_MCP_HISTORY", str(tmp_path))
    return load_plugin("plugin_recovery")


@pytest.fixture
def history(tmp_path):
    def _read():
        path = tmp_path / "history.jsonl"
        if not path.exists():
            return []
        return [json.loads(ln) for ln in path.read_text().splitlines() if ln]

    return _read


def kill_listener(server):
    """Break the listening socket so accept() raises, as a crash would."""
    server.socket.close()
    for _ in range(50):
        if not server.running:
            return
        time.sleep(0.1)
    raise AssertionError("accept loop did not notice the broken socket")


class TestListeningIsDerivedNotTracked:
    def test_reports_false_before_start(self, plugin):
        assert plugin.is_listening() is False

    def test_reports_true_while_running(self, plugin):
        assert plugin.start_socket_server(free_port()) is True
        try:
            assert plugin.is_listening() is True
        finally:
            plugin.stop_socket_server()

    def test_reports_false_after_a_clean_stop(self, plugin):
        plugin.start_socket_server(free_port())
        plugin.stop_socket_server()
        assert plugin.is_listening() is False

    def test_reports_false_once_the_thread_has_died(self, plugin):
        """The bug: a tracked flag stayed True, so the dialog showed green
        and restart refused, while nothing was bound."""
        plugin.start_socket_server(free_port())
        kill_listener(plugin.socket_server)
        assert plugin.is_listening() is False


class TestRestartAfterDeath:
    def test_start_socket_server_works_again(self, plugin):
        """The documented recovery used to be a no-op returning False."""
        first = free_port()
        assert plugin.start_socket_server(first) is True
        kill_listener(plugin.socket_server)

        second = free_port()
        assert plugin.start_socket_server(second) is True, (
            "restart must work after the listener died"
        )
        try:
            assert plugin.is_listening() is True
            with socket.create_connection(("localhost", second), timeout=5):
                pass
        finally:
            plugin.stop_socket_server()

    def test_starting_while_alive_is_still_refused(self, plugin):
        """Guarding against a second listener is the flag's real job."""
        plugin.start_socket_server(free_port())
        try:
            assert plugin.start_socket_server(free_port()) is False
        finally:
            plugin.stop_socket_server()


class TestDeathIsVisible:
    def test_unexpected_death_is_printed_regardless_of_verbose(self, plugin, capsys):
        """A dead listener cannot report itself through the socket, so this
        one message is exempt from the VERBOSE suppression."""
        assert plugin.VERBOSE is False, "test is meaningless if VERBOSE is on"
        plugin._report_listener_death(9876, RuntimeError("boom"))
        out = capsys.readouterr().out
        assert "9876" in out
        assert "boom" in out
        assert "start_socket_server" in out, "must say how to recover"

    def test_unexpected_death_is_recorded_on_disk(self, plugin, history):
        plugin.start_socket_server(free_port())
        kill_listener(plugin.socket_server)

        events = [r for r in history() if r.get("event") == "listener_died"]
        assert len(events) == 1, f"expected one death record, got {history()}"
        assert events[0]["ok"] is False

    def test_a_requested_stop_is_not_reported_as_death(self, plugin, history):
        """Only crashes are noise-worthy; a clean stop is expected."""
        plugin.start_socket_server(free_port())
        plugin.stop_socket_server()
        time.sleep(0.3)
        assert [r for r in history() if r.get("event") == "listener_died"] == []


class TestAcceptLoopSurvivesErrors:
    def test_a_handler_exception_does_not_end_the_listener(self, plugin):
        """One bad command used to be terminal for the whole session."""
        port = free_port()

        def exploding(command, args):
            raise RuntimeError("handler blew up")

        server = plugin.SocketServer(port=port)
        assert server.start(exploding)
        try:
            for _ in range(3):
                with socket.create_connection(("localhost", port), timeout=5) as sock:
                    sock.sendall(json.dumps({
                        "type": "structured_command",
                        "command": "show",
                        "args": {},
                        "source": "show cartoon",
                    }).encode())
                    sock.recv(65536)

            assert server.running is True, "listener must survive handler errors"
            with socket.create_connection(("localhost", port), timeout=5):
                pass
        finally:
            server.stop()
