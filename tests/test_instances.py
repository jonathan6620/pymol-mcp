"""Multiple PyMOL instances: port allocation, discovery, and targeting.

Every test pins the scanned range to OS-allocated free ports. Using the real
9876-9895 would make the suite discover a developer's own running PyMOL.
"""

import pytest
from conftest import free_ports, load_plugin

from pymol_mcp import server


@pytest.fixture
def ports():
    return free_ports(3)


@pytest.fixture
def scan_range(ports, monkeypatch):
    """Point the server's discovery at the test ports only."""
    monkeypatch.setattr(server, "PORT_RANGE", range(min(ports), max(ports) + 1))
    monkeypatch.setattr(server, "_connections", {})
    return ports


def start_instance(port, name):
    """A plugin instance listening on `port`, with a stub PyMOL behind it."""
    plugin = load_plugin(name)
    assert plugin.start_socket_server(port), f"could not claim {port}"
    return plugin


@pytest.fixture
def running(scan_range):
    """Start instances on request and stop them all afterwards."""
    started = []

    def _start(count):
        for i in range(count):
            started.append(start_instance(scan_range[i], f"inst{i}"))
        return started

    yield _start
    for plugin in started:
        plugin.stop_socket_server()


class TestTestPorts:
    """The fixture ports themselves, which were the source of a flaky suite."""

    def test_the_block_is_contiguous(self):
        """scan_range spans min..max, so a gap here is a scan over unrelated
        ports -- and it picks up listeners the test does not own."""
        got = free_ports(4)
        assert got == list(range(got[0], got[0] + 4))

    def test_the_scanned_range_is_only_the_test_ports(self, scan_range):
        assert len(server.PORT_RANGE) == len(scan_range)
        assert list(server.PORT_RANGE) == scan_range

    def test_the_block_avoids_the_real_pymol_range(self):
        """A developer's own PyMOL must never be discovered by the suite."""
        got = free_ports(3)
        assert not set(got) & set(range(9876, 9896))

    def test_port_range_stays_a_range(self, scan_range):
        """Narrowing the scan by assigning a *list* of the exact ports is the
        obvious-looking fix, and it breaks the no-instances error message,
        which formats PORT_RANGE.start and .stop. Allocating a contiguous
        block keeps the scan tight without giving up the range."""
        assert isinstance(server.PORT_RANGE, range)
        assert server.PORT_RANGE.start == scan_range[0]
        assert server.PORT_RANGE.stop - 1 == scan_range[-1]


class TestPortAllocation:
    def test_each_instance_claims_a_different_port(self, ports, monkeypatch):
        """A second PyMOL must get its own listener, not silently go without."""
        plugins = []
        try:
            for i in range(3):
                plugin = load_plugin(f"alloc{i}")
                monkeypatch.setattr(
                    plugin, "PORT_RANGE", range(min(ports), max(ports) + 1)
                )
                assert plugin.start_socket_server() is True
                plugins.append(plugin)
            claimed = [p.current_port for p in plugins]
            assert len(set(claimed)) == 3, f"ports collided: {claimed}"
        finally:
            for p in plugins:
                p.stop_socket_server()

    def test_returns_false_when_the_whole_range_is_taken(self, ports, monkeypatch):
        blocker = load_plugin("blocker")
        monkeypatch.setattr(blocker, "PORT_RANGE", range(ports[0], ports[0] + 1))
        assert blocker.start_socket_server() is True
        try:
            crowded = load_plugin("crowded")
            monkeypatch.setattr(crowded, "PORT_RANGE", range(ports[0], ports[0] + 1))
            assert crowded.start_socket_server() is False
        finally:
            blocker.stop_socket_server()

    def test_an_explicit_port_is_pinned_not_searched(self, ports):
        plugin = load_plugin("pinned")
        try:
            assert plugin.start_socket_server(ports[1]) is True
            assert plugin.current_port == ports[1]
        finally:
            plugin.stop_socket_server()


class TestDiscovery:
    def test_finds_nothing_when_no_pymol_is_running(self, scan_range):
        assert server.discover_instances() == []

    def test_finds_every_running_instance(self, running, scan_range):
        running(2)
        found = server.discover_instances()
        assert [i["port"] for i in found] == scan_range[:2]
        assert all(i["pid"] for i in found), "each instance should report its pid"

    def test_a_stopped_instance_disappears(self, scan_range):
        """Discovery is a scan, so a killed instance leaves nothing stale."""
        plugin = start_instance(scan_range[0], "transient")
        assert len(server.discover_instances()) == 1
        plugin.stop_socket_server()
        assert server.discover_instances() == []


class TestTargeting:
    def test_no_instances_is_an_actionable_error(self, scan_range):
        with pytest.raises(RuntimeError, match="No PyMOL is listening"):
            server.get_pymol_connection()

    def test_a_single_instance_is_selected_automatically(self, running, scan_range):
        running(1)
        assert server.get_pymol_connection().port == scan_range[0]

    def test_several_instances_refuse_to_guess(self, running, scan_range):
        """Driving the window the user is not watching looks like a no-op, so
        an ambiguous choice must fail loudly and name the options."""
        running(2)
        with pytest.raises(RuntimeError) as exc:
            server.get_pymol_connection()
        message = str(exc.value)
        assert "2 PyMOL instances" in message
        for port in scan_range[:2]:
            assert str(port) in message

    def test_commands_reach_only_the_targeted_instance(self, running, scan_range):
        first, second = running(2)
        conn = server.get_pymol_connection(scan_range[1])
        conn.send_command("refresh", {}, source="refresh")

        assert list(first.received_commands) == []
        assert list(second.received_commands) == ["refresh {}"]

    def test_connections_are_cached_per_port(self, running, scan_range):
        running(2)
        first_a = server.get_pymol_connection(scan_range[0])
        first_b = server.get_pymol_connection(scan_range[0])
        second = server.get_pymol_connection(scan_range[1])
        assert first_a is first_b, "the same port should reuse one connection"
        assert first_a is not second, "different ports need separate connections"
