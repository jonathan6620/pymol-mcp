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
from pathlib import Path

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

BOOTSTRAP = """
import importlib.util, os, sys

spec = importlib.util.spec_from_file_location("mcp_plugin", {plugin!r})
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

plugin.PORT_RANGE = range({lo}, {hi})
ok = plugin.start_socket_server()
print("LISTENING" if ok else "BIND_FAILED", plugin.current_port, flush=True)
"""


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
requires_pymol = pytest.mark.skipif(PYMOL is None, reason="no PyMOL executable found")


class Instance:
    """A real PyMOL process with the repo's plugin listening."""

    def __init__(self, workdir, index, history_dir, ports=TEST_PORTS):
        script = workdir / f"boot{index}.py"
        script.write_text(
            BOOTSTRAP.format(plugin=str(PLUGIN_PATH), lo=ports.start, hi=ports.stop)
        )
        env = dict(os.environ, PYMOL_MCP_HISTORY=str(history_dir))
        self.log = open(workdir / f"boot{index}.out", "w+")
        # -c headless, -q quiet, -K stay alive after the script finishes.
        # stdin must stay open: `-K` keeps PyMOL alive by reading commands from
        # stdin, so an inherited /dev/null gives instant EOF and it exits right
        # after printing LISTENING.
        self.proc = subprocess.Popen(
            [PYMOL, "-cqK", str(script)],
            stdin=subprocess.PIPE,
            stdout=self.log,
            stderr=subprocess.STDOUT,
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
            sock.sendall((json.dumps(payload) + "\n").encode())
            return json.loads(sock.makefile().readline())

    def objects(self):
        with socket.create_connection(("localhost", self.port), timeout=15) as sock:
            sock.sendall((json.dumps({"type": "instance_info"}) + "\n").encode())
            reply = json.loads(sock.makefile().readline())
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
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
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


##############################################################################
# Equivalence: typed tools versus the manual paths they replace
#
# These exist because the claims the typed settings tools rest on are about
# PyMOL's own behaviour, and a stub cmd cannot reproduce them. Setting layers,
# representation bits and what a .pse actually contains are only observable
# against a real process.
##############################################################################


def _one_instance(instances):
    return instances(1, KILL_PORTS)[0]


def _atom_value(inst, name, selection):
    """Read one atom's effective setting value through inspect_setting.

    Rounded because PyMOL stores settings as C floats: 0.8 reads back as
    0.800000011920929, which is the true stored value and not worth
    asserting to the bit.
    """
    reply = inst.send("inspect_setting", {"name": name, "selection": selection})
    assert reply["status"] == "success", reply
    data = reply["result"]["data"]
    assert data["values"], data
    return round(data["values"][0]["value"], 6)


def _prepare(inst, global_value=0.6, override=0.8):
    """A fragment with a global setting and one atom overriding it."""
    inst.send("fragment", {"name": "ala"})
    inst.send("show", {"representation": "cartoon", "selection": "ala"})
    inst.send("set", {"setting": "cartoon_transparency", "value": str(global_value)})
    inst.send(
        "set",
        {
            "setting": "cartoon_transparency",
            "value": str(override),
            "selection": "ala and name CA",
        },
    )


@requires_pymol
class TestSettingLayers:
    """The skill's clearing table, executed instead of asserted in prose.

    Two of these forms report success and change nothing, which is why the
    typed tool sends an explicit scope rather than passing a selection through
    and hoping its shape addresses the layer the caller meant.
    """

    # Each row is (selection written by hand, does it clear the atom layer).
    # One test rather than five parametrised ones: every case needs the same
    # freshly prepared state, and starting a PyMOL per row costs far more than
    # the isolation is worth.
    CLEARING_TABLE = [
        ("ala", False),          # bare object name addresses the object layer
        ("all", False),          # bare `all` is still the object layer
        ("(ala)", True),         # parenthesised addresses the atoms
        ("(all)", True),         # ...so `(all)` IS a working blanket reset
        ("ala and name CA", True),
    ]

    def test_only_atom_scoped_forms_clear_an_atom_override(self, instances):
        inst = _one_instance(instances)
        results = {}
        for clearer, _ in self.CLEARING_TABLE:
            _prepare(inst)
            assert _atom_value(inst, "cartoon_transparency", "ala and name CA") == 0.8
            reply = inst.send(
                "unset", {"setting": "cartoon_transparency", "selection": clearer}
            )
            assert reply["status"] == "success", reply
            results[clearer] = _atom_value(
                inst, "cartoon_transparency", "ala and name CA"
            )

        expected = {
            clearer: (0.6 if clears else 0.8)
            for clearer, clears in self.CLEARING_TABLE
        }
        assert results == expected

    def test_the_typed_scope_clears_where_the_bare_name_does_not(self, instances):
        """The equivalence that matters: same selection, scope makes it work."""
        inst = _one_instance(instances)
        _prepare(inst)

        inst.send(
            "unset",
            {"setting": "cartoon_transparency", "selection": "ala", "scope": "object"},
        )
        assert _atom_value(inst, "cartoon_transparency", "ala and name CA") == 0.8

        inst.send(
            "unset",
            {"setting": "cartoon_transparency", "selection": "ala", "scope": "atom"},
        )
        assert _atom_value(inst, "cartoon_transparency", "ala and name CA") == 0.6

    def test_a_cleared_setting_no_longer_reports_as_overridden(self, instances):
        """`overridden` is the field a caller acts on, so it has to settle.

        The two layers are read by different PyMOL calls that disagree about
        float width -- the atoms widen a stored C float to a double, so a
        global of 0.6 comes back as 0.6000000238418579. Compared raw, this
        reports an override on a scene that has none.
        """
        inst = _one_instance(instances)
        _prepare(inst)

        before = inst.send(
            "inspect_setting",
            {"name": "cartoon_transparency", "selection": "ala"},
        )["result"]["data"]
        assert before["overridden"] is True

        inst.send(
            "unset",
            {"setting": "cartoon_transparency", "selection": "ala", "scope": "atom"},
        )
        after = inst.send(
            "inspect_setting",
            {"name": "cartoon_transparency", "selection": "ala"},
        )["result"]["data"]
        assert after["overridden"] is False
        assert after["uniform"] is True


@requires_pymol
class TestUnsetDiffersFromSetZero:
    def test_setting_zero_pins_where_unset_restores_inheritance(self, instances):
        """The documented workaround and the new command are not the same thing.

        Overwriting with 0 leaves an atom-level entry pinned at 0; clearing
        removes it so the atom inherits the layer beneath. Invisible while the
        global is 0, which is why the difference went unnoticed.
        """
        inst = _one_instance(instances)
        _prepare(inst)

        inst.send(
            "set",
            {
                "setting": "cartoon_transparency",
                "value": "0",
                "selection": "ala and name CA",
            },
        )
        assert _atom_value(inst, "cartoon_transparency", "ala and name CA") == 0.0

        inst.send(
            "set",
            {
                "setting": "cartoon_transparency",
                "value": "0.8",
                "selection": "ala and name CA",
            },
        )
        inst.send(
            "unset",
            {
                "setting": "cartoon_transparency",
                "selection": "ala and name CA",
                "scope": "atom",
            },
        )
        assert _atom_value(inst, "cartoon_transparency", "ala and name CA") == 0.6


@requires_pymol
class TestInspectSettingSeesWhatGetSettingCannot:
    def test_get_setting_reads_clean_while_an_override_is_live(self, instances):
        """The failure the tool exists for, both readings side by side."""
        inst = _one_instance(instances)
        _prepare(inst, global_value=0.0, override=0.8)

        plain = inst.send("get_setting", {"name": "cartoon_transparency"})
        assert json.loads(plain["result"]["output"])["value"] == "0.00000"

        data = inst.send(
            "inspect_setting",
            {"name": "cartoon_transparency", "selection": "ala"},
        )["result"]["data"]
        assert data["overridden"] is True
        assert 0.8 in [round(g["value"], 6) for g in data["values"]]
        assert data["uniform"] is False


@requires_pymol
class TestRepresentationBits:
    def test_the_copied_bit_table_still_matches_pymol(self, tmp_path_factory):
        """The hardcoded table is copied for offline testability, so this is
        the only thing standing between it and silent rot.

        Run inside PyMOL rather than here: pymol.viewing is importable only in
        a PyMOL interpreter, and the test venv has no PyMOL at all.
        """
        workdir = tmp_path_factory.mktemp("bits")
        script = workdir / "bits.py"
        script.write_text(
            "import importlib.util, json\n"
            "from pymol import viewing\n"
            "spec = importlib.util.spec_from_file_location("
            f"'p', {str(PLUGIN_PATH)!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "live = {n: i for n, i in viewing.repres.items() if i >= 0}\n"
            "print('MATCH=' + json.dumps(module.REP_BITS == live))\n"
            "print('LIVE=' + json.dumps(live, sort_keys=True))\n"
            "print('TABLE=' + json.dumps(module.REP_BITS, sort_keys=True))\n"
        )
        proc = subprocess.run(
            [PYMOL, "-cq", str(script)], capture_output=True, text=True, timeout=120
        )
        assert proc.returncode == 0, proc.stderr
        assert "MATCH=true" in proc.stdout, (
            "the plugin's REP_BITS no longer matches pymol.viewing.repres, so "
            "representation names decode wrongly:\n" + proc.stdout
        )

    def test_hiding_and_showing_is_visible_to_the_tool(self, instances):
        inst = _one_instance(instances)
        inst.send("fragment", {"name": "ala"})

        inst.send("hide", {"representation": "everything", "selection": "ala"})
        out = inst.send("get_representations", {})["result"]["data"]
        assert out["hidden"] is True
        assert out["reps"] == []

        inst.send("show", {"representation": "cartoon", "selection": "ala"})
        out = inst.send("get_representations", {})["result"]["data"]
        assert "cartoon" in out["reps"]
        assert out["hidden"] is False

    def test_a_rep_on_part_of_a_group_is_reported_partial(self, instances):
        inst = _one_instance(instances)
        inst.send("fragment", {"name": "ala"})
        inst.send("hide", {"representation": "everything", "selection": "ala"})
        inst.send("show", {"representation": "spheres", "selection": "ala and name CA"})

        out = inst.send("get_representations", {})["result"]["data"]
        group = out["groups"][0]
        assert group["partial"] is True
        assert [entry["rep"] for entry in group["per_rep"]] == ["spheres"]
        assert group["per_rep"][0]["atoms"] < group["atoms"]


@requires_pymol
class TestHistoryEquivalence:
    def test_the_tool_returns_what_reading_the_file_returns(
        self, instances, tmp_path_factory
    ):
        """Also proves the read happens where the file is.

        The history directory comes from PYMOL_MCP_HISTORY in the environment
        PyMOL was launched from, which the server does not share. A server-side
        read would consult its own environment and quietly answer from the
        wrong file, or from ~/.pymol-mcp.
        """
        inst = _one_instance(instances)
        inst.send("fragment", {"name": "ala"}, source="fragment ala")
        inst.send("show", {"representation": "cartoon", "selection": "ala"},
                  source="show cartoon, ala")

        out = inst.send("get_history", {"limit": 50})["result"]["data"]
        directory = Path(out["directory"])
        on_disk = [
            json.loads(line)
            for line in (directory / "history.jsonl").read_text().splitlines()
            if line
        ]

        assert out["entries"] == on_disk
        assert [e["source"] for e in out["entries"]][:2] == [
            "fragment ala",
            "show cartoon, ala",
        ]
        assert not str(directory).startswith(str(Path.home() / ".pymol-mcp"))

    def test_failed_only_finds_the_command_that_did_not_work(self, instances):
        inst = _one_instance(instances)
        inst.send("fragment", {"name": "ala"}, source="fragment ala")
        inst.send("load", {"filename": "/nonexistent/nope.pdb"},
                  source="load /nonexistent/nope.pdb")

        out = inst.send("get_history", {"failed_only": True})["result"]["data"]
        assert out["entries"], "the failed load was not recorded as failed"
        assert all(entry["ok"] is False for entry in out["entries"])

    def test_reading_the_history_does_not_appear_in_it(self, instances):
        inst = _one_instance(instances)
        inst.send("fragment", {"name": "ala"}, source="fragment ala")
        inst.send("get_history", {}, source="get_history")

        out = inst.send("get_history", {}, source="get_history")["result"]["data"]
        assert [e.get("command") for e in out["entries"]] == ["fragment"]


@requires_pymol
class TestSaveMetadataMatchesAFreshProcess:
    def test_reported_objects_match_what_reopening_the_file_finds(
        self, instances, tmp_path_factory
    ):
        """The .pse verification ritual, run once here instead of by hand.

        Saving, reopening in a fresh PyMOL and counting objects is what the
        skill told the caller to do every time. The counts come back with the
        save now, and this is what proves they are the same counts.
        """
        inst = _one_instance(instances)
        inst.send("fragment", {"name": "ala"})
        inst.send("fragment", {"name": "trp"})

        workdir = tmp_path_factory.mktemp("save")
        target = workdir / "session.pse"
        reply = inst.send("save_file", {"filename": str(target)})
        assert reply["status"] == "success", reply
        meta = reply["result"]["data"]

        assert target.exists()
        assert meta["bytes"] == target.stat().st_size
        assert meta["path"] == str(target)
        assert meta["format"] == "pse"
        assert sorted(meta["objects"]) == ["ala", "trp"]
        assert sorted(meta["objects_verified"]) == ["ala", "trp"]

        # The fresh process: plain -cq, no -K, no plugin.
        script = workdir / "verify.py"
        script.write_text(
            "from pymol import cmd\n"
            f"cmd.load({str(target)!r})\n"
            "print('OBJECTS=' + ','.join(sorted(cmd.get_object_list('all'))))\n"
            "print('ATOMS=%d' % cmd.count_atoms('all'))\n"
        )
        proc = subprocess.run(
            [PYMOL, "-cq", str(script)], capture_output=True, text=True, timeout=120
        )
        assert proc.returncode == 0, proc.stderr

        reopened = dict(
            line.split("=", 1)
            for line in proc.stdout.splitlines()
            if line.startswith(("OBJECTS=", "ATOMS="))
        )
        assert sorted(reopened["OBJECTS"].split(",")) == sorted(meta["objects"])
        assert int(reopened["ATOMS"]) == meta["atoms"]
