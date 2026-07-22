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

    def test_installs_a_symlink_to_the_checkout(self, mod, home):
        mod.main()
        dest = home / ".claude" / "skills" / mod.SKILL_NAME
        assert dest.is_symlink()
        assert dest.resolve() == (REPO_ROOT / "skills" / mod.SKILL_NAME).resolve()
        assert (dest / "SKILL.md").is_file(), "skill must be readable through the link"

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

    def test_remove_existing_clears_a_symlink_without_following_it(
        self, mod, tmp_path
    ):
        target = tmp_path / "real"
        target.mkdir()
        (target / "keep.txt").write_text("x")
        link = tmp_path / "link"
        link.symlink_to(target, target_is_directory=True)

        assert mod.remove_existing(str(link)) is True
        assert not link.exists()
        assert (target / "keep.txt").is_file(), "must not delete the link target"

    def test_remove_existing_clears_a_copied_directory(self, mod, tmp_path):
        stale = tmp_path / "stale"
        stale.mkdir()
        (stale / "__init__.py").write_text("old")
        assert mod.remove_existing(str(stale)) is True
        assert not stale.exists()

    def test_remove_existing_reports_nothing_to_do(self, mod, tmp_path):
        assert mod.remove_existing(str(tmp_path / "absent")) is False

    def test_copy_ignores_junk(self, mod):
        ignored = mod.COPY_IGNORE("dir", [".git", "__pycache__", ".DS_Store", "a.py"])
        assert "a.py" not in ignored
        assert {".git", "__pycache__", ".DS_Store"} <= set(ignored)


class TestScriptsAreSelfContained:
    """The install scripts must run before `uv sync`, so stdlib only."""

    @pytest.mark.parametrize(
        "stem", ["install_plugin", "install_pymolrc", "install_skill"]
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
