# Install scripts

One script per platform. Each takes a freshly cloned repo to a working
PyMOL-MCP setup, installing whatever is missing along the way.

| Platform | Command |
|---|---|
| macOS | `./shell/install-macos.sh` |
| Linux | `./shell/install-linux.sh` |
| Windows | `powershell -ExecutionPolicy Bypass -File shell\install-windows.ps1` |
| WSL | `./shell/install-linux.sh` — see [WSL notes](#wsl-notes) |

```bash
git clone https://github.com/jonathan6620/pymol-mcp
cd pymol-mcp
./shell/install-macos.sh
```

`common.sh` holds the shared logic for the two Unix scripts and is sourced, not
run. The Windows script is standalone.

## What they do

1. **uv** — install it if missing (Homebrew where available, otherwise
   Astral's installer).
2. **`uv sync`** — this repo's Python dependencies, from `uv.lock`, into
   `.venv`.
3. **PyMOL** — search PATH and the usual conda, `PyMOL.app`, and Schrödinger
   locations. If nothing turns up, create the `pymol-env` conda environment
   from `environment.yml`, first installing Miniforge if there is no conda at
   all. PyMOL is not on PyPI, which is why conda is involved.
4. **Socket plugin** — `pymol -cq scripts/install_plugin.py`.
5. **Auto-start** — add the listener block to `~/.pymolrc.py`.
6. **Skill** — install `skills/pymol-mcp/` for Claude Code and Codex.
7. **MCP client** — register the server with whichever of the `claude` and
   `codex` CLIs is on PATH.

Every step is idempotent, so rerunning after a partial failure is the normal
way to resume. Nothing is installed system-wide and nothing needs `sudo`:
Miniforge goes to `~/miniforge3`, uv to `~/.local/bin`.

## Options

| Unix | Windows | Effect |
|---|---|---|
| `-y`, `--yes` | `-Yes` | Answer yes to every prompt — unattended runs and CI |
| `--pymol PATH` | `-Pymol PATH` | Use this PyMOL instead of searching |
| `--skip-pymol` | `-SkipPymol` | Do not look for or install PyMOL |
| `--skip-clients` | `-SkipClients` | Do not touch your MCP client config |
| `--force-pymolrc` | `-ForcePymolrc` | Overwrite an unmanaged `~/.pymolrc.py` |
| `-h`, `--help` | `Get-Help .\shell\install-windows.ps1` | Usage |

With no terminal attached — a pipe, a CI job — the prompts answer themselves
with yes, so `curl … | bash` behaves like `--yes`.

## When a step is skipped

A run that cannot find or install PyMOL still does everything else, then
prints what is left to do by hand. The plugin step is the only one that needs
PyMOL, and it can be run on its own afterwards:

```bash
pymol -cq scripts/install_plugin.py
```

## Testing them

```bash
make test-install           # ~1 minute
make test-install FULL=1    # adds the conda path: ~1 GB, several minutes
```

`test-install.sh` runs `install-linux.sh` inside a throwaway Ubuntu container,
which is the only honest way to test it — the script installs uv, edits
`~/.pymolrc.py`, and creates conda environments, none of which you want
happening on the machine running the test. Needs Docker; nothing is installed
locally.

The fast suite covers the guards (missing `curl`, an unusable `--pymol`, an
unknown flag), a bootstrap from a machine with nothing installed, and a rerun
proving idempotency. `FULL=1` adds Miniforge, the `pymol-env` environment, the
plugin install, and driving a headless PyMOL over the socket — it fetches 1UBQ
and checks that ubiquitin's 76 CA atoms come back, so it exercises the whole
path rather than just opening a port. It needs network access to the PDB.

Most assertions are regression tests for bugs that only appeared when the
scripts were run rather than read, and each is commented with what it guards.
This is deliberately not part of CI or of `make validate`: it needs Docker and
takes minutes. Run it after touching `shell/`, `scripts/install_*.py`, or
`environment.yml`.

## Relationship to the Makefile

`make install` does steps 4–6 and assumes uv, PyMOL, and the dependencies are
already in place. These scripts are the superset for a machine starting from
nothing, and they call the same `scripts/install_*.py` that `make` does, so
the two cannot drift.

## Windows notes

- PowerShell 5.1 or newer. Unblock the script for one run with
  `-ExecutionPolicy Bypass` rather than changing the machine-wide policy.
- Miniforge's silent installer takes its target directory as an unquoted `/D=`
  argument, so a profile path containing a space will not work; install conda
  yourself first if that applies.
- If you configure Claude Desktop by hand afterwards, use forward slashes in
  `claude_desktop_config.json`.

## WSL notes

Use `install-linux.sh` inside WSL — not `install-windows.ps1`. WSL2 is real
Linux, so the script behaves exactly as it does on any Ubuntu.

The rule that matters: **keep PyMOL, the MCP server, and your client all
inside WSL.** The plugin binds `localhost:9876` and the server connects to
`localhost`, so both ends have to share a network namespace. Running the
script inside WSL gets this right on its own, because it registers whichever
`claude` or `codex` CLI is on PATH there.

Driving a WSL PyMOL from Claude Desktop on Windows is the configuration to
avoid. Claude Desktop launches the MCP server on the Windows side, pointed at
Windows loopback. WSL2's `localhostForwarding` sometimes bridges that, but not
dependably for a listener bound to `127.0.0.1` rather than `0.0.0.0` — and the
server's scan across ports 9876-9895 then fails in a way that looks like
random disconnects rather than a misconfiguration.

Two smaller things:

- **No display needed for setup or for driving PyMOL.** With `DISPLAY` unset,
  PyMOL started with `pymol -cKq` still loads `~/.pymolrc.py`, starts the
  listener, and answers commands. You only need WSLg (Windows 11) or an X
  server to *see* the viewport.
- **Headless, you will not see the startup banner.** The README tells you to
  look for `MCP socket plugin auto-started on port 9876`, but PyMOL writes
  nothing to a redirected stdout under `-c`. Check the listener directly:

  ```bash
  ss -ltn | grep 987
  ```

Clone into your WSL home rather than `/mnt/c`. Symlinks are not the reason —
they point *at* the repo and land in the Linux filesystem either way — but
DrvFs is slow enough to be noticeable on `uv sync`.
