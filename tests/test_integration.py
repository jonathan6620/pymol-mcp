"""End-to-end tests against real PyMOL processes.

Everything else in the suite drives the plugin in-process with a stub PyMOL.
These launch actual PyMOL instances and talk to them over real sockets, which
is the only way to catch the failure this exists to prevent: a second PyMOL
silently having no listener, so commands land in the window the user is not
looking at.

Opt in, because each instance takes several seconds to start:

    make test-integration

Skipped automatically when no PyMOL executable can be found.
"""

import glob
import json
import os
import shutil
import socket
import subprocess
import time

import pytest
from conftest import PLUGIN_PATH, free_ports

from pymol_mcp import server

pytestmark = pytest.mark.integration

# Deliberately not 9876-9895: a developer with PyMOL open would otherwise have
# their own live instance discovered, and possibly driven, by the test suite.
TEST_PORTS = range(9930, 9934)
# The module-scoped pair stays alive for the whole module, so tests that start
# their own instances need a range of their own or discovery counts both sets.
KILL_PORTS = range(9934, 9938)

BOOTSTRAP = '''
import importlib.util, os, sys

spec = importlib.util.spec_from_file_location("mcp_plugin", {plugin!r})
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

plugin.PORT_RANGE = range({lo}, {hi})
ok = plugin.start_socket_server()
print("LISTENING" if ok else "BIND_FAILED", plugin.current_port, flush=True)
'''


def find_pymol():
    """Locate a PyMOL executable the same way the Makefile does."""
    found = shutil.which("pymol")
    if found:
        return found
    patterns = [
        "~/*conda*/envs/*/bin/pymol",
        "/opt/*conda*/envs/*/bin/pymol",
        "/opt/homebrew/Caskroom/*/base/envs/*/bin/pymol",
        "/usr/local/*conda*/envs/*/bin/pymol",
        "/Applications/PyMOL.app/Contents/bin/pymol",
    ]
    for pattern in patterns:
        for hit in glob.glob(os.path.expanduser(pattern)):
            if os.access(hit, os.X_OK):
                return hit
    return None


PYMOL = find_pymol()
requires_pymol = pytest.mark.skipif(
    PYMOL is None, reason="no PyMOL executable found"
)


class Instance:
    """A real PyMOL process with the repo's plugin listening."""

    def __init__(self, workdir, index, history_dir, ports=TEST_PORTS):
        script = workdir / f"boot{index}.py"
        script.write_text(
            BOOTSTRAP.format(
                plugin=str(PLUGIN_PATH), lo=ports.start, hi=ports.stop
            )
        )
        env = dict(os.environ, PYMOL_MCP_HISTORY=str(history_dir))
        self.log = open(workdir / f"boot{index}.out", "w+")
        # -c headless, -q quiet, -K stay alive after the script finishes.
        # stdin must stay open: `-K` keeps PyMOL alive by reading commands from
        # stdin, so an inherited /dev/null gives instant EOF and it exits right
        # after printing LISTENING.
        self.proc = subprocess.Popen(
            [PYMOL, "-cqK", str(script)],
            stdin=subprocess.PIPE, stdout=self.log, stderr=subprocess.STDOUT,
            env=env,
        )
        self.port = self._await_port(workdir / f"boot{index}.out")

    def _await_port(self, logpath, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(f"PyMOL exited early:\n{logpath.read_text()}")
            text = logpath.read_text()
            for line in text.splitlines():
                if line.startswith("LISTENING"):
                    return int(line.split()[1])
                if line.startswith("BIND_FAILED"):
                    raise AssertionError(f"plugin could not bind:\n{text}")
            time.sleep(0.2)
        raise AssertionError(f"PyMOL did not start listening:\n{logpath.read_text()}")

    def send(self, command, args, source="test"):
        payload = {
            "type": "structured_command",
            "command": command,
            "args": args,
            "source": source,
        }
        with socket.create_connection(("localhost", self.port), timeout=15) as sock:
            sock.sendall(json.dumps(payload).encode())
            return json.loads(sock.recv(65536).decode())

    def objects(self):
        with socket.create_connection(("localhost", self.port), timeout=15) as sock:
            sock.sendall(json.dumps({"type": "instance_info"}).encode())
            reply = json.loads(sock.recv(65536).decode())
        return reply["result"]["objects"]

    def stop(self):
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.terminate()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=15)
        self.log.close()


@pytest.fixture(scope="module")
def scan_range():
    """Point the server's discovery at the test range for the whole module."""
    original = server.PORT_RANGE
    server.PORT_RANGE = TEST_PORTS
    yield TEST_PORTS
    server.PORT_RANGE = original


@pytest.fixture(scope="module")
def two_instances(tmp_path_factory, scan_range):
    """Two real PyMOLs, started once and shared. Starting them is slow."""
    workdir = tmp_path_factory.mktemp("pymol")
    history = tmp_path_factory.mktemp("history")
    started = []
    try:
        for i in range(2):
            started.append(Instance(workdir, i, history))
        yield started
    finally:
        for inst in started:
            inst.stop()


@pytest.fixture
def instances(tmp_path_factory):
    """Start instances on demand and guarantee they are stopped.

    A hand-written try/finally leaked a live PyMOL when a test failed before
    its cleanup line, and the orphan then squatted on a test port and broke
    every later run.
    """
    workdir = tmp_path_factory.mktemp("pymol_adhoc")
    history = tmp_path_factory.mktemp("history_adhoc")
    started = []

    def _start(count, ports):
        for i in range(count):
            name = f"{ports.start}_{i}"
            started.append(Instance(workdir, name, history, ports=ports))
        return started

    yield _start
    for inst in started:
        try:
            inst.stop()
        except Exception:
            inst.proc.kill()


@pytest.fixture(autouse=True)
def clean_connection_cache(scan_range):
    server._connections.clear()
    yield
    server._connections.clear()


@requires_pymol
class TestRealInstancesGetSeparatePorts:
    def test_each_instance_claims_its_own_port(self, two_instances):
        ports = [inst.port for inst in two_instances]
        assert len(set(ports)) == 2, f"two PyMOLs shared a port: {ports}"
        assert all(p in TEST_PORTS for p in ports)

    def test_both_are_alive_and_accepting(self, two_instances):
        for inst in two_instances:
            with socket.create_connection(("localhost", inst.port), timeout=15):
                pass

    def test_discovery_finds_both(self, two_instances):
        found = server.discover_instances()
        assert sorted(i["port"] for i in found) == sorted(
            inst.port for inst in two_instances
        )
        pids = [i["pid"] for i in found]
        assert len(set(pids)) == 2, f"instances reported the same pid: {pids}"

    def test_reported_pids_are_the_real_processes(self, two_instances):
        by_port = {i["port"]: i["pid"] for i in server.discover_instances()}
        for inst in two_instances:
            assert by_port[inst.port] == inst.proc.pid


@requires_pymol
class TestCommandsDoNotLeakBetweenInstances:
    def test_an_object_loaded_in_one_is_absent_from_the_other(self, two_instances):
        """The whole point: a command must not land in the other window."""
        first, second = two_instances
        reply = first.send("fragment", {"name": "ala"})
        assert reply["status"] == "success", reply

        assert "ala" in first.objects()
        assert "ala" not in second.objects(), (
            "a command sent to one PyMOL appeared in the other"
        )

    def test_targeting_by_port_reaches_the_right_instance(self, two_instances):
        first, second = two_instances
        conn = server.get_pymol_connection(second.port)
        conn.send_command("fragment", {"name": "trp"}, source="fragment trp")

        assert "trp" in second.objects()
        assert "trp" not in first.objects()

    def test_deleting_in_one_leaves_the_other_alone(self, two_instances):
        """Same object name in both, deleted from one only."""
        first, second = two_instances
        for inst in (first, second):
            assert inst.send("fragment", {"name": "gly"})["status"] == "success"

        first.send("delete", {"name": "gly"})

        assert "gly" not in first.objects()
        assert "gly" in second.objects(), "delete crossed instances"


@requires_pymol
class TestAmbiguityIsRefused:
    def test_unset_instance_with_two_running_is_an_error(self, two_instances):
        with pytest.raises(RuntimeError) as exc:
            server.get_pymol_connection()
        message = str(exc.value)
        assert "2 PyMOL instances" in message
        for inst in two_instances:
            assert str(inst.port) in message

    def test_list_instances_distinguishes_them_by_contents(self, two_instances):
        """Loaded objects are how a human tells the windows apart."""
        first, second = two_instances
        assert first.send("fragment", {"name": "his"})["status"] == "success"

        found = {i["port"]: i["objects"] for i in server.discover_instances()}
        assert "his" in found[first.port]
        assert "his" not in found[second.port]


@requires_pymol
class TestSurvivingInstanceKeepsWorking:
    def test_killing_one_leaves_the_other_discoverable(self, instances, monkeypatch):
        monkeypatch.setattr(server, "PORT_RANGE", KILL_PORTS)
        doomed, survivor = instances(2, KILL_PORTS)

        assert len(server.discover_instances()) == 2

        doomed.proc.kill()
        doomed.proc.wait(timeout=15)

        deadline = time.time() + 20
        while time.time() < deadline:
            found = server.discover_instances()
            if len(found) == 1:
                break
            time.sleep(0.5)

        assert [i["port"] for i in found] == [survivor.port], (
            "a killed instance must leave nothing stale behind"
        )
        # With one left, the choice is unambiguous again.
        server._connections.clear()
        assert server.get_pymol_connection().port == survivor.port


@requires_pymol
class TestPortExhaustion:
    def test_more_instances_than_ports_fails_loudly(self, tmp_path_factory):
        """One free port, two PyMOLs: the second must report BIND_FAILED
        rather than appearing to start."""
        workdir = tmp_path_factory.mktemp("pymol_full")
        history = tmp_path_factory.mktemp("history_full")
        (only,) = free_ports(1)

        script = workdir / "boot_one.py"
        script.write_text(
            BOOTSTRAP.format(plugin=str(PLUGIN_PATH), lo=only, hi=only + 1)
        )
        env = dict(os.environ, PYMOL_MCP_HISTORY=str(history))
        logs = [open(workdir / f"one{i}.out", "w+") for i in range(2)]
        procs = [
            subprocess.Popen(
                [PYMOL, "-cqK", str(script)],
                stdout=log, stderr=subprocess.STDOUT, env=env,
            )
            for log in logs
        ]
        try:
            deadline = time.time() + 60
            outcomes = []
            while time.time() < deadline and len(outcomes) < 2:
                outcomes = []
                for i in range(2):
                    text = (workdir / f"one{i}.out").read_text()
                    for line in text.splitlines():
                        if line.startswith(("LISTENING", "BIND_FAILED")):
                            outcomes.append(line.split()[0])
                            break
                time.sleep(0.3)

            assert sorted(outcomes) == ["BIND_FAILED", "LISTENING"], (
                f"expected exactly one winner, got {outcomes}"
            )
        finally:
            for proc in procs:
                proc.kill()
                proc.wait(timeout=15)
            for log in logs:
                log.close()
