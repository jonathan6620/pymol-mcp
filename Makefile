# PyMOL-MCP setup and development tasks.
#
# PyMOL often is not on PATH -- conda installs it inside an env, and a shell
# alias does not help here because make runs recipes with /bin/sh, which does
# not read shell aliases. So look for the executable in the usual places, and
# let the user override:
#
#     make install PYMOL=/full/path/to/pymol
#
# `origin` keeps this to a single lookup, and skips it entirely when PYMOL is
# passed on the command line.
ifeq ($(origin PYMOL), undefined)
PYMOL := $(shell \
	command -v pymol 2>/dev/null || \
	for p in "$$CONDA_PREFIX"/bin/pymol \
	         $$HOME/*conda*/envs/*/bin/pymol \
	         $$HOME/*forge*/envs/*/bin/pymol \
	         $$HOME/*conda*/bin/pymol \
	         /opt/homebrew/Caskroom/*/base/envs/*/bin/pymol \
	         /opt/*conda*/envs/*/bin/pymol \
	         /usr/local/*conda*/envs/*/bin/pymol \
	         /Applications/PyMOL.app/Contents/bin/pymol ; do \
	  [ -x "$$p" ] && { echo "$$p"; break; } ; \
	done)
endif

FORCE ?=

.PHONY: help install install-plugin install-pymolrc install-skill test \
        test-integration test-install lint typecheck validate

help:
	@echo "Targets:"
	@echo "  install           Install the plugin, the pymolrc auto-start, and the skill"
	@echo "  install-plugin    Symlink the socket plugin into PyMOL's plugin directory"
	@echo "  install-pymolrc   Add the auto-start block to ~/.pymolrc.py"
	@echo "  install-skill     Install the usage skill for Claude Code and Codex"
	@echo "  test              Run the test suite"
	@echo "  test-integration  Run tests against real PyMOL processes (slow)"
	@echo "  test-install      Test the shell/ install scripts in a container (needs Docker)"
	@echo "  lint              Run ruff"
	@echo "  typecheck         Run Pyright"
	@echo "  validate          Run lint, type checks, and tests"
	@echo ""
	@echo "Variables:"
	@echo "  PYMOL=<path>      PyMOL executable (auto-detected; a shell alias will not work)"
	@echo "  FORCE=1           install-pymolrc: replace an unmanaged ~/.pymolrc.py"
	@echo "  FULL=1            test-install: also test the conda path (slow, ~1 GB)"
	@echo ""
	@echo "Detected PYMOL: $(if $(PYMOL),$(PYMOL),<none - pass PYMOL=/path/to/pymol>)"

install: install-plugin install-pymolrc install-skill
	@echo ""
	@echo "Done. Restart PyMOL; it will report the port its listener claimed."

install-plugin:
	@if [ -z "$(PYMOL)" ]; then \
	  echo "error: could not find a pymol executable."; \
	  echo ""; \
	  echo "Looked on PATH and in the usual conda / PyMOL.app locations."; \
	  echo "Pass the full path explicitly:"; \
	  echo ""; \
	  echo "    make $(MAKECMDGOALS) PYMOL=/full/path/to/pymol"; \
	  echo ""; \
	  echo "A shell alias will not work: make runs recipes with /bin/sh,"; \
	  echo "which does not read shell aliases. In zsh, 'which pymol' shows"; \
	  echo "the path an alias points at."; \
	  exit 1; \
	fi
	$(PYMOL) -cq scripts/install_plugin.py

install-pymolrc:
	uv run python scripts/install_pymolrc.py $(if $(FORCE),--force,)

install-skill:
	uv run python scripts/install_skill.py

test:
	uv run pytest

# Launches real PyMOL instances, so it is minutes not seconds.
test-integration:
	uv run pytest -m integration -v

# Runs the shell/ install scripts in a throwaway container, which is the only
# way to test them: they install uv, edit ~/.pymolrc.py and create conda
# environments. Needs Docker. FULL=1 adds the conda and PyMOL path, which
# pulls ~1 GB and fetches a structure from the PDB.
test-install:
	./shell/test-install.sh $(if $(FULL),--full,)

lint:
	uv run ruff check .

typecheck:
	uv run pyright src/pymol_mcp

validate: lint typecheck test
