"""The plugin's on-disk session history."""

import json

from conftest import load_plugin


class TestCommandHistory:
    """The plugin's on-disk session history."""

    @staticmethod
    def _load_plugin(monkeypatch, setting):
        """Import a fresh plugin instance with PYMOL_MCP_HISTORY applied.

        The setting is read at import time, so each case needs its own module
        object rather than a shared import.
        """
        monkeypatch.setenv("PYMOL_MCP_HISTORY", str(setting))
        return load_plugin("plugin_history")

    @staticmethod
    def _records(tmp_path):
        text = (tmp_path / "history.jsonl").read_text()
        return [json.loads(line) for line in text.splitlines() if line]

    @staticmethod
    def _script(tmp_path):
        scripts = list(tmp_path.glob("session-*.pml"))
        assert len(scripts) == 1, f"expected one session script, got {scripts}"
        return scripts[0].read_text()

    def test_successful_command_is_recorded_and_replayable(self, tmp_path, monkeypatch):
        plugin = self._load_plugin(monkeypatch, tmp_path)
        plugin._record_history(
            "fetch", {"code": "1ubq"}, "fetch 1ubq", {"executed": True}
        )
        record = self._records(tmp_path)[0]
        assert record["command"] == "fetch"
        assert record["source"] == "fetch 1ubq"
        assert record["ok"] is True
        assert "fetch 1ubq" in self._script(tmp_path)

    def test_failed_command_is_logged_but_kept_out_of_the_script(
        self, tmp_path, monkeypatch
    ):
        plugin = self._load_plugin(monkeypatch, tmp_path)
        plugin._record_history(
            "show",
            {"representation": "cartoon", "selection": "chain Z"},
            "show cartoon, chain Z",
            {"executed": False, "error": "Invalid selection"},
        )
        record = self._records(tmp_path)[0]
        assert record["ok"] is False
        assert record["error"] == "Invalid selection"
        # A command that failed would not replay, so it must not be in the script.
        assert "show cartoon" not in self._script(tmp_path)

    def test_untrusted_source_is_logged_but_not_replayable(
        self, tmp_path, monkeypatch
    ):
        plugin = self._load_plugin(monkeypatch, tmp_path)
        injected = "refresh; run /tmp/untrusted.pml"
        plugin._record_history("refresh", {}, injected, {"executed": True})

        record = self._records(tmp_path)[0]
        assert record["source"] == injected
        assert record["replayable"] is False
        assert injected not in self._script(tmp_path)

    def test_source_must_match_the_executed_command(self, tmp_path, monkeypatch):
        plugin = self._load_plugin(monkeypatch, tmp_path)
        plugin._record_history(
            "refresh", {}, "run /tmp/untrusted.pml", {"executed": True}
        )

        assert self._records(tmp_path)[0]["replayable"] is False
        assert "run /tmp/untrusted.pml" not in self._script(tmp_path)

    def test_internal_call_without_source_is_not_recorded(self, tmp_path, monkeypatch):
        """The connection health-check ping must not pollute the history."""
        plugin = self._load_plugin(monkeypatch, tmp_path)
        plugin._record_history("refresh", {}, None, {"executed": True})
        assert list(tmp_path.iterdir()) == []

    def test_off_setting_writes_nothing(self, tmp_path, monkeypatch):
        plugin = self._load_plugin(monkeypatch, "off")
        plugin._record_history(
            "fetch", {"code": "1ubq"}, "fetch 1ubq", {"executed": True}
        )
        assert list(tmp_path.iterdir()) == []

    def test_relative_load_path_is_recorded_absolute(self, tmp_path, monkeypatch):
        """PyMOL resolves relative paths against its own cwd, which is lost later."""
        plugin = self._load_plugin(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        plugin._record_history(
            "load", {"filename": "dimer.pdb"}, "load dimer.pdb", {"executed": True}
        )
        record = self._records(tmp_path)[0]
        assert record["file"]["direction"] == "in"
        assert record["file"]["path"] == str(tmp_path / "dimer.pdb")

    def test_output_files_are_recorded_with_direction_out(self, tmp_path, monkeypatch):
        plugin = self._load_plugin(monkeypatch, tmp_path)
        plugin._record_history(
            "png", {"filename": "/tmp/figure.png"}, "png /tmp/figure.png",
            {"executed": True},
        )
        assert self._records(tmp_path)[0]["file"] == {
            "path": "/tmp/figure.png",
            "direction": "out",
        }

    def test_command_without_a_file_argument_has_no_file_field(
        self, tmp_path, monkeypatch
    ):
        plugin = self._load_plugin(monkeypatch, tmp_path)
        plugin._record_history(
            "show", {"representation": "cartoon"}, "show cartoon", {"executed": True}
        )
        assert "file" not in self._records(tmp_path)[0]

    def test_write_failure_disables_history_instead_of_raising(
        self, tmp_path, monkeypatch
    ):
        """History must never be able to break command execution."""
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("")
        plugin = self._load_plugin(monkeypatch, blocker / "history")

        plugin._record_history(
            "fetch", {"code": "1ubq"}, "fetch 1ubq", {"executed": True}
        )

        assert plugin._history_broken is True
