"""The plugin's socket server: binding, framing, and dispatch."""

import json
import socket

import pytest
from conftest import load_plugin


def free_port():
    """Ask the OS for an unused port, then release it."""
    s = socket.socket()
    s.bind(("localhost", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def plugin():
    return load_plugin("plugin_socket")


@pytest.fixture
def server(plugin):
    """A running SocketServer with a stub PyMOL, stopped on teardown."""
    started = []

    def handler(command, args):
        if args.get("selection") == "chain Z":
            return {"executed": False, "error": "Invalid selection"}
        return {"executed": True, "output": f"ran {command}"}

    srv = plugin.SocketServer(port=free_port())
    assert srv.start(handler), "server failed to bind"
    started.append(srv)
    yield srv
    for s in started:
        s.stop()


def send(port, payload, timeout=5):
    """Send one JSON message and return the decoded reply."""
    with socket.create_connection(("localhost", port), timeout=timeout) as sock:
        sock.sendall(json.dumps(payload).encode("utf-8"))
        return json.loads(sock.recv(65536).decode("utf-8"))


class TestSocketServerLifecycle:
    def test_start_reports_success_and_listens(self, server):
        with socket.create_connection(("localhost", server.port), timeout=5):
            pass  # connecting at all proves the socket is bound and listening

    def test_start_on_an_occupied_port_returns_false(self, plugin, server):
        """Regression: start() used to bind in the worker thread and so
        returned True for a port it had not actually acquired."""
        second = plugin.SocketServer(port=server.port)
        assert second.start(lambda c, a: None) is False
        assert second.socket is None

    def test_start_twice_on_one_server_returns_false(self, server):
        assert server.start(lambda c, a: None) is False

    def test_stop_releases_the_port(self, plugin, server):
        port = server.port
        server.stop()
        reuser = plugin.SocketServer(port=port)
        try:
            assert reuser.start(lambda c, a: None) is True
        finally:
            reuser.stop()


class TestSocketServerDispatch:
    def test_successful_command_returns_success(self, server):
        reply = send(server.port, {
            "type": "structured_command",
            "command": "fetch",
            "args": {"code": "1ubq"},
            "source": "fetch 1ubq",
        })
        assert reply["status"] == "success"
        assert reply["result"]["output"] == "ran fetch"

    def test_handler_failure_becomes_an_error_reply(self, server):
        reply = send(server.port, {
            "type": "structured_command",
            "command": "show",
            "args": {"representation": "cartoon", "selection": "chain Z"},
            "source": "show cartoon, chain Z",
        })
        assert reply["status"] == "error"
        assert "Invalid selection" in reply["message"]

    def test_unknown_message_type_is_rejected(self, server):
        reply = send(server.port, {"type": "exec", "command": "rm -rf /"})
        assert reply["status"] == "error"
        assert "Unknown message type" in reply["message"]

    def test_request_without_source_still_executes(self, server):
        """The health-check ping carries no source; it must still work."""
        reply = send(server.port, {
            "type": "structured_command",
            "command": "refresh",
            "args": {},
        })
        assert reply["status"] == "success"

    def test_partial_json_is_buffered_until_complete(self, server):
        """Messages larger than one recv must be reassembled, not dropped."""
        payload = json.dumps({
            "type": "structured_command",
            "command": "select",
            "args": {"name": "big", "selection": "x" * 6000},
            "source": "select big, ...",
        }).encode("utf-8")
        with socket.create_connection(("localhost", server.port), timeout=5) as sock:
            sock.sendall(payload[:100])
            sock.sendall(payload[100:])
            reply = json.loads(sock.recv(65536).decode("utf-8"))
        assert reply["status"] == "success"
