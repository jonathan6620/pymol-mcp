import subprocess

import pymol_mcp.server as server


class FakeProcess:
    def __init__(self, pid=4242, returncode=None):
        self.pid = pid
        self.returncode = returncode

    def poll(self):
        return self.returncode


def test_launch_retains_process_and_waits_for_matching_listener(monkeypatch):
    process = FakeProcess()
    popen_calls = []
    discoveries = iter(
        [
            [{"port": 9876, "pid": 10, "objects": []}],
            [
                {"port": 9876, "pid": 10, "objects": []},
                {"port": 9877, "pid": process.pid, "objects": []},
            ],
        ]
    )

    monkeypatch.setattr(server, "_find_pymol_executable", lambda: "/opt/pymol")
    monkeypatch.setattr(server, "discover_instances", lambda: next(discoveries))

    def fake_popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return process

    monkeypatch.setattr(server.subprocess, "Popen", fake_popen)
    server._launched_processes.clear()

    instance = server._launch_pymol_process()

    assert instance["port"] == 9877
    assert server._launched_processes == {process.pid: process}
    command, kwargs = popen_calls[0]
    assert command == ["/opt/pymol", "-q"]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["start_new_session"] is (server.os.name != "nt")


def test_launch_reports_early_exit(monkeypatch):
    process = FakeProcess(returncode=7)
    monkeypatch.setattr(server, "_find_pymol_executable", lambda: "/opt/pymol")
    monkeypatch.setattr(server, "discover_instances", lambda: [])
    monkeypatch.setattr(server.subprocess, "Popen", lambda *args, **kwargs: process)
    server._launched_processes.clear()

    try:
        server._launch_pymol_process()
    except RuntimeError as error:
        assert "exit status 7" in str(error)
    else:
        raise AssertionError("an early PyMOL exit must fail the launch")

    assert process.pid not in server._launched_processes


def test_launch_tool_returns_actionable_error(monkeypatch):
    monkeypatch.setattr(
        server,
        "_launch_pymol_process",
        lambda timeout: (_ for _ in ()).throw(RuntimeError("not installed")),
    )

    assert server._launch_pymol() == "PyMOL launch failed: not installed"
