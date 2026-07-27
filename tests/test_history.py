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


class TestReadingTheHistoryBack:
    """`get_history` against the file it reads.

    The equivalence claim: the tool answers what `tail -20 history.jsonl`,
    `grep '"ok": false'` and `grep '"command": "load"'` answered, without
    needing a shell on the machine PyMOL is running on. Each test compares the
    handler's output against the same filtering done by hand over the file.
    """

    @staticmethod
    def _plugin(monkeypatch, setting, name="plugin_history_read"):
        monkeypatch.setenv("PYMOL_MCP_HISTORY", str(setting))
        return load_plugin(name)

    @staticmethod
    def _by_hand(tmp_path):
        text = (tmp_path / "history.jsonl").read_text()
        return [json.loads(line) for line in text.splitlines() if line]

    def _write(self, plugin, count=5):
        for i in range(count):
            ok = i % 2 == 0
            plugin._record_history(
                "load" if ok else "show",
                {"filename": "/tmp/f%d.pdb" % i} if ok else {},
                "load /tmp/f%d.pdb" % i if ok else "show cartoon",
                {"executed": ok} if ok else {"executed": False, "error": "boom"},
            )

    def test_entries_match_reading_the_file_by_hand(self, tmp_path, monkeypatch):
        plugin = self._plugin(monkeypatch, tmp_path)
        self._write(plugin)
        out = plugin.build_command_dispatcher(object())["get_history"]({"limit": 20})
        assert out["entries"] == self._by_hand(tmp_path)
        assert out["enabled"] is True
        assert out["directory"] == str(tmp_path)

    def test_limit_returns_the_tail(self, tmp_path, monkeypatch):
        plugin = self._plugin(monkeypatch, tmp_path)
        self._write(plugin, count=10)
        out = plugin.build_command_dispatcher(object())["get_history"]({"limit": 3})
        assert out["entries"] == self._by_hand(tmp_path)[-3:]
        assert out["total"] == 10
        assert out["truncated"] is True

    def test_failed_only_matches_grepping_for_ok_false(self, tmp_path, monkeypatch):
        plugin = self._plugin(monkeypatch, tmp_path)
        self._write(plugin)
        out = plugin.build_command_dispatcher(object())["get_history"](
            {"failed_only": True}
        )
        assert out["entries"] == [
            r for r in self._by_hand(tmp_path) if r["ok"] is False
        ]
        assert out["entries"]

    def test_command_filter_matches_grepping_for_that_command(
        self, tmp_path, monkeypatch
    ):
        plugin = self._plugin(monkeypatch, tmp_path)
        self._write(plugin)
        out = plugin.build_command_dispatcher(object())["get_history"](
            {"command": "load"}
        )
        assert out["entries"] == [
            r for r in self._by_hand(tmp_path) if r.get("command") == "load"
        ]
        # This is what answers "where did that file go" -- absolute, even
        # though the recorded source used whatever the caller typed.
        assert out["entries"][0]["file"]["path"].startswith("/")

    def test_filters_apply_before_the_limit(self, tmp_path, monkeypatch):
        """`limit` must mean "last N matching", as `grep ... | tail -N` does.

        Applying the limit first would return the tail of everything and then
        filter it, so a `command="load"` search could come back empty purely
        because recent traffic was something else.
        """
        plugin = self._plugin(monkeypatch, tmp_path)
        plugin._record_history("load", {}, "load a.pdb", {"executed": True})
        for i in range(30):
            plugin._record_history("show", {}, "show cartoon", {"executed": True})
        out = plugin.build_command_dispatcher(object())["get_history"](
            {"command": "load", "limit": 5}
        )
        assert [r["source"] for r in out["entries"]] == ["load a.pdb"]

    def test_a_truncated_final_record_is_skipped_not_fatal(
        self, tmp_path, monkeypatch
    ):
        """A half-written line is what a crash leaves, and recovery is when
        this gets read."""
        plugin = self._plugin(monkeypatch, tmp_path)
        self._write(plugin, count=2)
        with open(tmp_path / "history.jsonl", "a") as fh:
            fh.write('{"ts": "2026-01-01T00:00:00", "comm')
        out = plugin.build_command_dispatcher(object())["get_history"]({})
        assert len(out["entries"]) == 2

    def test_reading_does_not_create_a_session_script(self, tmp_path, monkeypatch):
        """The reader must not have the writer's side effects.

        _history_paths writes a session-*.pml header as soon as it is called,
        so a reader built on it would fabricate a replay script in a PyMOL that
        had run nothing.
        """
        plugin = self._plugin(monkeypatch, tmp_path)
        plugin.build_command_dispatcher(object())["get_history"]({})
        assert list(tmp_path.glob("session-*.pml")) == []
        assert not (tmp_path / "history.jsonl").exists()

    def test_reading_the_history_is_not_itself_recorded(self, tmp_path, monkeypatch):
        """Otherwise every poll pushes what you are looking for further away."""
        plugin = self._plugin(monkeypatch, tmp_path)
        self._write(plugin, count=2)
        plugin._record_history("get_history", {}, "get_history", {"executed": True})
        assert [r["command"] for r in self._by_hand(tmp_path)] == ["load", "show"]

    def test_history_switched_off_reports_disabled_rather_than_raising(
        self, tmp_path, monkeypatch
    ):
        plugin = self._plugin(monkeypatch, "off", name="plugin_history_off")
        out = plugin.build_command_dispatcher(object())["get_history"]({})
        assert out == {
            "enabled": False,
            "directory": None,
            "script": None,
            "entries": [],
            "total": 0,
            "truncated": False,
        }

    def test_a_missing_history_file_is_empty_not_an_error(
        self, tmp_path, monkeypatch
    ):
        plugin = self._plugin(monkeypatch, tmp_path / "nothing-here")
        out = plugin.build_command_dispatcher(object())["get_history"]({})
        assert out["enabled"] is True
        assert out["entries"] == []
