"""The scripts in scripts/: pymolrc splicing, skill and plugin installation.

These write into HOME, so every test redirects HOME at tmp_path first. A test
that touched the real home directory would rewrite the developer's own config.
"""

from pathlib import Path

import pytest
from conftest import REPO_ROOT, load_script


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point HOME at a temporary directory for the duration of a test."""
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    return fake


class TestInstallPymolrc:
    @pytest.fixture
    def mod(self):
        return load_script("install_pymolrc")

    def test_target_is_dot_pymolrc_in_home(self, mod, home):
        assert mod.target_path() == str(home / ".pymolrc.py")

    def test_block_is_delimited_by_both_markers(self, mod):
        assert mod.BLOCK.startswith(mod.BEGIN)
        assert mod.BLOCK.rstrip().endswith(mod.END)

    def test_appends_to_a_config_with_no_managed_block(self, mod):
        text, action = mod.splice("import pymol\n")
        assert "import pymol" in text, "existing config must be preserved"
        assert mod.BEGIN in text
        assert action == "appended block to"

    def test_replaces_an_existing_block_in_place(self, mod):
        original = f"before\n{mod.BEGIN}\nstale = 1\n{mod.END}\nafter\n"
        text, action = mod.splice(original)
        assert action == "updated block in"
        assert "stale = 1" not in text
        assert "before" in text and "after" in text
        assert text.count(mod.BEGIN) == 1, "must not accumulate duplicate blocks"

    def test_is_idempotent(self, mod):
        once, _ = mod.splice("")
        twice, _ = mod.splice(once)
        assert once == twice

    def test_unterminated_block_is_refused(self, mod):
        """Appending past a half-written block would corrupt the file."""
        with pytest.raises(SystemExit, match="no closing"):
            mod.splice(f"{mod.BEGIN}\nhalf written\n")


class TestInstallSkill:
    @pytest.fixture
    def mod(self):
        return load_script("install_skill")

    def test_source_is_the_skill_in_this_checkout(self, mod):
        assert Path(mod.source_dir()) == REPO_ROOT / "skills" / mod.SKILL_NAME

    def test_destination_is_created_under_home(self, mod, home):
        dest = mod.dest_dir()
        assert Path(dest) == home / ".claude" / "skills" / mod.SKILL_NAME
        assert (home / ".claude" / "skills").is_dir()

        codex_dest = mod.codex_dest_dir()
        assert Path(codex_dest) == home / ".agents" / "skills" / mod.SKILL_NAME
        assert (home / ".agents" / "skills").is_dir()

    def test_installs_a_symlink_to_the_checkout(self, mod, home):
        mod.main()
        dest = home / ".claude" / "skills" / mod.SKILL_NAME
        assert dest.is_symlink()
        assert dest.resolve() == (REPO_ROOT / "skills" / mod.SKILL_NAME).resolve()
        assert (dest / "SKILL.md").is_file(), "skill must be readable through the link"
        codex_dest = home / ".agents" / "skills" / mod.SKILL_NAME
        assert codex_dest.is_symlink()
        assert codex_dest.resolve() == (REPO_ROOT / "skills" / mod.SKILL_NAME).resolve()

    def test_rerun_is_idempotent(self, mod, home, capsys):
        mod.main()
        capsys.readouterr()
        mod.main()
        assert "Already linked" in capsys.readouterr().out

    def test_an_existing_real_directory_is_backed_up_not_deleted(self, mod, home):
        dest = home / ".claude" / "skills" / mod.SKILL_NAME
        dest.mkdir(parents=True)
        (dest / "SKILL.md").write_text("hand written, must survive")

        mod.main()

        backups = list(dest.parent.glob(f"{mod.SKILL_NAME}.bak-*"))
        assert len(backups) == 1, "previous skill must be kept"
        assert (backups[0] / "SKILL.md").read_text() == "hand written, must survive"
        assert dest.is_symlink()


class TestInstallMcp:
    """Client registration, and specifically that it reconciles rather than
    assumes. `mcp get` answers "is there an entry", never "is the entry
    correct"; trusting it left every client pointing at the old flat
    pymol_mcp_server.py after the console-script move, reported on every rerun
    as "already registered"."""

    # Real `codex mcp get --json` output, trimmed. The command sits under
    # "transport" -- reading the top level made every entry look unparseable.
    CODEX_JSON = """
    {"name": "pymol", "enabled": true,
     "transport": {"type": "stdio", "command": "uv",
                   "args": ["--directory", "/repo", "run", "--frozen", "pymol-mcp"]}}
    """

    # Real `claude mcp get` output, trimmed. No --json exists on this one.
    CLAUDE_TEXT = """pymol:
  Scope: User config (available in all your projects)
  Status: OK Connected
  Type: stdio
  Command: uv
  Args: --directory /repo run --frozen pymol-mcp
"""

    @pytest.fixture
    def mod(self):
        return load_script("install_mcp")

    class Recorder(list):
        """The argv list of every CLI call, plus scripted replies.

        A list subclass so tests can compare it directly, with `replies`
        mapping an argv prefix to the (exit code, output) that call returns.
        """

        replies = None

    @pytest.fixture
    def calls(self, mod, monkeypatch):
        """Record every CLI invocation, and script the exit code and output."""
        recorded = self.Recorder()
        recorded.replies = {}

        def fake_run(argv):
            recorded.append(argv)
            for prefix, reply in recorded.replies.items():
                if argv[: len(prefix)] == list(prefix):
                    return reply
            return 0, ""

        monkeypatch.setattr(mod, "run", fake_run)
        monkeypatch.setattr(mod.shutil, "which", lambda cli: f"/usr/bin/{cli}")
        return recorded

    def client(self, mod, cli):
        return next(c for c in mod.CLIENTS if c.cli == cli)

    def test_expected_command_runs_the_console_script_via_uv(self, mod):
        assert mod.expected_argv("/repo") == [
            "uv",
            "--directory",
            "/repo",
            "run",
            "--frozen",
            "pymol-mcp",
        ]

    def test_repo_root_is_this_checkout(self, mod):
        assert Path(mod.repo_root()) == REPO_ROOT

    def test_parses_the_claude_text_format(self, mod):
        assert mod.parse_claude(self.CLAUDE_TEXT) == (
            "uv",
            "--directory /repo run --frozen pymol-mcp",
        )

    def test_parses_the_codex_transport_block(self, mod):
        assert mod.parse_codex(self.CODEX_JSON) == (
            "uv",
            "--directory /repo run --frozen pymol-mcp",
        )

    def test_codex_top_level_command_still_parses(self, mod):
        """Fallback for the shape flattening again."""
        flat = '{"command": "uv", "args": ["--directory", "/repo"]}'
        assert mod.parse_codex(flat) == ("uv", "--directory /repo")

    @pytest.mark.parametrize("junk", ["", "not json", "[]", "{}", "null"])
    def test_unreadable_output_does_not_parse(self, mod, junk):
        assert mod.parse_codex(junk) is None

    def test_missing_entry_is_absent(self, mod, calls):
        calls.replies[("codex", "mcp", "get")] = (1, "no such server")
        assert self.client(mod, "codex").state("/repo")[0] == mod.ABSENT

    def test_matching_entry_is_present(self, mod, calls):
        calls.replies[("codex", "mcp", "get")] = (0, self.CODEX_JSON)
        assert self.client(mod, "codex").state("/repo")[0] == mod.PRESENT

    def test_the_old_flat_script_entry_is_stale(self, mod, calls):
        """The exact regression: a registration naming the pre-restructure
        command must be detected, not accepted."""
        stale = self.CODEX_JSON.replace(
            '"run", "--frozen", "pymol-mcp"', '"run", "--quiet", "pymol_mcp_server.py"'
        )
        calls.replies[("codex", "mcp", "get")] = (0, stale)
        state, found = self.client(mod, "codex").state("/repo")
        assert state == mod.STALE
        assert "pymol_mcp_server.py" in found

    def test_a_registration_without_frozen_is_stale(self, mod, calls):
        """The lock-churn regression: the client spawns this command with its
        own environment, so the Makefile's UV_FROZEN export never reaches it.
        A registration predating --frozen rewrites uv.lock on every start and
        must be replaced, not accepted."""
        stale = self.CODEX_JSON.replace('"run", "--frozen"', '"run"')
        calls.replies[("codex", "mcp", "get")] = (0, stale)
        state, found = self.client(mod, "codex").state("/repo")
        assert state == mod.STALE
        assert "--frozen" not in found

    def test_a_moved_checkout_is_stale(self, mod, calls):
        """The other way the recorded --directory goes stale."""
        calls.replies[("codex", "mcp", "get")] = (0, self.CODEX_JSON)
        assert self.client(mod, "codex").state("/moved/elsewhere")[0] == mod.STALE

    def test_unparseable_entry_is_stale_not_present(self, mod, calls):
        """Degrade into "fix it", never into "assume it is fine"."""
        calls.replies[("codex", "mcp", "get")] = (0, "surprise new format")
        assert self.client(mod, "codex").state("/repo")[0] == mod.STALE

    def test_a_stale_entry_is_removed_before_being_re_added(self, mod, calls):
        """`mcp add` refuses an existing name, so the remove is load-bearing."""
        calls.replies[("codex", "mcp", "get")] = (0, "unreadable")
        self.client(mod, "codex").reconcile("/repo")

        verbs = [c[2] for c in calls if c[:2] == ["codex", "mcp"]]
        assert verbs == ["get", "remove", "add"], verbs

    def test_a_correct_entry_is_left_alone(self, mod, calls):
        calls.replies[("codex", "mcp", "get")] = (0, self.CODEX_JSON)
        message = self.client(mod, "codex").reconcile("/repo")

        verbs = [c[2] for c in calls if c[:2] == ["codex", "mcp"]]
        assert verbs == ["get"], "a correct entry must not be rewritten"
        assert "already registered" in message

    def test_claude_is_registered_at_user_scope(self, mod, calls):
        """User scope on purpose: PyMOL gets driven from whatever directory
        holds the structures, not from this checkout."""
        calls.replies[("claude", "mcp", "get")] = (1, "not found")
        self.client(mod, "claude").reconcile("/repo")

        add = next(c for c in calls if c[:3] == ["claude", "mcp", "add"])
        assert add[4:6] == ["-s", "user"]
        assert add[6] == "--", "the -- guards the server argv from the CLI's parser"
        assert add[7:] == mod.expected_argv("/repo")

    def test_a_failed_add_is_reported_not_swallowed(self, mod, calls):
        calls.replies[("codex", "mcp", "get")] = (1, "absent")
        calls.replies[("codex", "mcp", "add")] = (1, "boom: could not write config")
        message = self.client(mod, "codex").reconcile("/repo")
        assert "FAILED" in message
        assert "boom" in message

    def test_an_absent_cli_is_skipped_not_an_error(
        self, mod, calls, capsys, monkeypatch
    ):
        calls.replies[("claude", "mcp", "get")] = (1, "absent")
        present = {"claude": "/usr/bin/claude", "codex": None}
        monkeypatch.setattr(mod.shutil, "which", lambda cli: present[cli])

        assert mod.main([]) == 0
        assert "Not on PATH, skipped: Codex" in capsys.readouterr().out

    def test_no_cli_at_all_prints_manual_instructions(
        self, mod, calls, capsys, monkeypatch
    ):
        monkeypatch.setattr(mod.shutil, "which", lambda cli: None)

        assert mod.main([]) == 0
        out = capsys.readouterr().out
        assert "claude mcp add pymol -s user -- uv --directory" in out
        assert "codex mcp add pymol -- uv --directory" in out

    def test_skip_clients_touches_nothing(self, mod, calls, capsys):
        assert mod.main(["--skip-clients"]) == 0
        assert calls == [], "--skip-clients must not invoke any CLI"
        assert "Skipping" in capsys.readouterr().out

    def test_run_never_raises_when_the_cli_is_missing(self, mod):
        """Real subprocess: a nonexistent binary must come back as a failure."""
        code, text = mod.run(["definitely-not-a-real-cli-xyz", "mcp", "get"])
        assert code == 1
        assert text


class TestMakefileWiringMatchesTheInstallers:
    """`make install` shipped without registering the server at all, which is
    how the client config came to be hand-written and then went stale."""

    def test_install_depends_on_install_mcp(self):
        makefile = (REPO_ROOT / "Makefile").read_text()
        target = next(
            line for line in makefile.splitlines() if line.startswith("install:")
        )
        assert "install-mcp" in target, "make install must register the MCP server"

    def test_every_install_path_delegates_to_the_shared_script(self):
        """Three entry points, one implementation -- they drifted before."""
        for path in ("Makefile", "shell/common.sh", "shell/install-windows.ps1"):
            text = (REPO_ROOT / path).read_text()
            assert "install_mcp.py" in text, f"{path} must call the shared script"

    def test_every_entry_point_pins_uv_frozen(self):
        """Without this, an installer whose uv is older than the one that wrote
        uv.lock rewrites the lock in place, so running the installer leaves an
        ~800-line diff in the user's checkout."""
        for path in ("Makefile", "shell/common.sh", "shell/install-windows.ps1"):
            text = (REPO_ROOT / path).read_text()
            assert "UV_FROZEN" in text, f"{path} must pin UV_FROZEN"

    def test_the_installers_do_not_call_mcp_add_directly(self):
        """A direct `mcp add` here is the presence-only check growing back."""
        for path in ("shell/common.sh", "shell/install-windows.ps1"):
            text = (REPO_ROOT / path).read_text()
            for line in text.splitlines():
                if line.lstrip().startswith("#") or line.lstrip().startswith(
                    "Add-Note"
                ):
                    continue
                assert "mcp add pymol" not in line, (
                    f"{path} must reconcile via install_mcp.py, not add directly"
                )


class TestInstallPlugin:
    @pytest.fixture
    def mod(self, monkeypatch):
        # The module runs main() on import so that `pymol -cq` works; suppress
        # that here so the helpers can be tested on their own.
        monkeypatch.setenv("PYMOL_MCP_INSTALLER_NO_RUN", "1")
        return load_script("install_plugin")

    def test_source_is_the_plugin_in_this_checkout(self, mod):
        source = Path(mod.repo_plugin_dir())
        assert source == REPO_ROOT / mod.PLUGIN_DIRNAME
        assert (source / "__init__.py").is_file()

    def test_module_name_matches_pymol_startup_convention(self, mod):
        """PyMOL imports plugins as pmg_tk.startup.<directory name>."""
        assert mod.MODULE_NAME == "pmg_tk.startup." + mod.PLUGIN_DIRNAME

    def test_clear_removes_a_symlink_without_following_it(self, mod, tmp_path):
        target = tmp_path / "real"
        target.mkdir()
        (target / "keep.txt").write_text("x")
        link = tmp_path / "link"
        link.symlink_to(target, target_is_directory=True)

        assert mod.clear(str(link)) is None
        assert not link.exists()
        assert (target / "keep.txt").is_file(), "must not delete the link target"

    def test_clear_backs_up_a_copied_directory(self, mod, tmp_path):
        stale = tmp_path / "stale"
        stale.mkdir()
        (stale / "__init__.py").write_text("old")
        saved = mod.clear(str(stale))
        assert not stale.exists()
        assert Path(saved).name.startswith("stale.bak-")
        assert (Path(saved) / "__init__.py").read_text() == "old"

    def test_clear_reports_nothing_to_do(self, mod, tmp_path):
        assert mod.clear(str(tmp_path / "absent")) is None

    def test_copy_ignores_junk(self, mod):
        ignored = mod.COPY_IGNORE("dir", [".git", "__pycache__", ".DS_Store", "a.py"])
        assert "a.py" not in ignored
        assert {".git", "__pycache__", ".DS_Store"} <= set(ignored)


class TestScriptsAreSelfContained:
    """The install scripts must run before `uv sync`, so stdlib only."""

    @pytest.mark.parametrize(
        "stem", ["install_plugin", "install_pymolrc", "install_skill", "install_mcp"]
    )
    def test_no_third_party_imports(self, stem):
        source = (REPO_ROOT / "scripts" / f"{stem}.py").read_text()
        for banned in ("import pydantic", "import mcp", "import requests"):
            assert banned not in source, f"{stem}.py must depend on the stdlib only"

    def test_no_file_shadows_pymols_config_search(self):
        """PyMOL's get_user_config() scans the working directory before HOME and
        stops at the first match, so a file named pymolrc* anywhere we launch
        PyMOL from would silently shadow the user's real config."""
        offenders = [
            p.name
            for p in list(REPO_ROOT.iterdir()) + list((REPO_ROOT / "scripts").iterdir())
            if p.name.lstrip(".").startswith("pymolrc")
        ]
        assert offenders == [], f"these would shadow ~/.pymolrc: {offenders}"
