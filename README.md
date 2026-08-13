# PyMOL-MCP: Control PyMOL with Claude or OpenAI Codex

PyMOL-MCP connects PyMOL to AI clients through the Model Context Protocol
(MCP), enabling Claude and OpenAI Codex to directly interact with and control
PyMOL. It supports conversational structural biology, molecular visualization,
and analysis through natural language.


## Features

- **Two-way communication**: Connect Claude or Codex to PyMOL through an MCP server
- **Intelligent command parsing**: Natural language processing for PyMOL commands
- **Molecular visualization control**: Manipulate representations, colors, and views
- **Structural analysis**: Perform measurements, alignments, and other analyses
- **No arbitrary code execution**: Only allowlisted `cmd.*` calls are dispatched, with no `exec()` or `eval()`

## Prerequisites

- PyMOL — see [Step 0](#step-0-install-pymol)
- Claude Desktop, Claude Code, or OpenAI Codex
- Git
- Make, if you want to use the [Quick Start](#quick-start)

## Quick Start

One script does the whole setup, installing uv, PyMOL, the plugin, the skill,
and the MCP client registration — whichever of those is missing:

```bash
git clone https://github.com/jonathan6620/pymol-mcp
cd pymol-mcp
./shell/install-macos.sh        # or ./shell/install-linux.sh
```

On Windows:

```powershell
powershell -ExecutionPolicy Bypass -File shell\install-windows.ps1
```

It is safe to re-run, and `shell/README.md` documents the flags — `--skip-pymol`
if you already have PyMOL, `--skip-clients` to leave your MCP config alone.

### Quick Start by hand

For Claude Code, with [uv](#step-1-install-the-uv-package-manager), *conda* and
*Make* installed:

```bash
git clone https://github.com/jonathan6620/pymol-mcp
cd pymol-mcp
conda env create -f environment.yml     # installs PyMOL; skip if you have it
conda activate pymol-env
uv sync
claude mcp add pymol -s user -- uv --directory $(pwd) run --frozen pymol-mcp
make install
```

For OpenAI Codex, replace the `claude mcp add` command with:

```bash
codex mcp add pymol -- uv --directory "$(pwd)" run --frozen pymol-mcp
```

Restart PyMOL and start a new Claude Code session. On startup PyMOL prints
`MCP socket plugin auto-started on port 9876`, or the next free port.

If `make` cannot find the PyMOL executable, then pass the path:
`make install PYMOL=/full/path/to/pymol`.

For Claude Desktop, use [Step 3, Option A](#option-a-claude-desktop) in place of
the `claude mcp add` line, then run `make install`.

## Full Installation Guide

### Step 0: Install PyMOL

```bash
conda env create -f environment.yml
conda activate pymol-env
```

That installs `pymol-open-source` from conda-forge — no licence key, no expiry.
Schrödinger's "incentive" build works too, but needs a licence file; nothing in
this server's command table depends on its extras.

Any other PyMOL install works as well; `make` will find it, or you can pass
`PYMOL=/full/path/to/pymol`.

PyMOL keeps its own Python, separate from this repo's `.venv` — the two talk
over a socket, so they never need the same packages.

### Step 1: Install the uv Package Manager

**On macOS/Linux:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Or, on macOS with Homebrew:

```bash
brew install uv
```

**On Windows:**

```bash
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
set Path=C:\Users\[YourUsername]\.local\bin;%Path%
```

For other platforms, visit the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).

### Step 2: Clone the Repository

```bash
git clone https://github.com/jonathan6620/pymol-mcp
cd pymol-mcp
uv sync
```

### Step 3: Configure your MCP client

Use Claude Desktop, Claude Code, or OpenAI Codex.

#### Option A: Claude Desktop

1. Open Claude Desktop
2. Go to Claude > Settings > Developer > Edit Config
3. This will open the `claude_desktop_config.json` file
4. Add the MCP server configuration:

```json
{
  "mcpServers": {
    "pymol": {
      "command": "[Full path to uv]",
      "args": [
        "--directory",
        "[Full path to the cloned pymol-mcp repo]",
        "run",
        "pymol-mcp"
      ]
    }
  }
}
```

For example:

```json
{
  "mcpServers": {
    "pymol": {
      "command": "/Users/username/.local/bin/uv",
      "args": [
        "--directory",
        "/Users/username/pymol-mcp",
        "run",
        "pymol-mcp"
      ]
    }
  }
}
```

> **Note:** Ensure that you specify the full paths for your system. Run `which uv` on macOS/Linux
> or `where uv` (Windows) to find the uv binary, since Claude Desktop does not
> inherit your shell's `PATH`. On Windows, use forward slashes (/) instead of
> backslashes.

#### Option B: Claude Code (CLI)

From the cloned repository directory, add the PyMOL MCP server using the `claude` CLI:

```bash
claude mcp add pymol -s user -- uv --directory $(pwd) run --frozen pymol-mcp
```

`$(pwd)` expands to the repo you're standing in, so run this from the `pymol-mcp`
directory you cloned in Step 2. From anywhere else, pass the full path instead:

```bash
claude mcp add pymol -s user -- uv --directory /path/to/pymol-mcp run --frozen pymol-mcp
```

This saves the configuration to `~/.claude.json`. You can verify it was added with:

```bash
claude mcp list
```

> **Note:** After adding the MCP server, you must restart your Claude Code session for the tools to become available.

#### Option C: OpenAI Codex

From the cloned repository directory, register the local stdio MCP server:

```bash
codex mcp add pymol -- uv --directory "$(pwd)" run --frozen pymol-mcp
```

Verify the configuration with `codex mcp list`. Codex stores MCP configuration
in `~/.codex/config.toml`; the Codex CLI, IDE extension, and ChatGPT desktop app
on the same Codex host share it. Restart the client after adding the server.

The equivalent manual configuration is:

```toml
[mcp_servers.pymol]
command = "uv"
args = ["--directory", "/full/path/to/pymol-mcp", "run", "pymol-mcp"]
```

### Step 4: Install the PyMOL Socket Plugin

The MCP server communicates with PyMOL over a socket. Each PyMOL claims its own
port in the range 9876-9895, so several instances can run at once. Install the
socket listener plugin from the repository you cloned in Step 2:

```bash
pymol -cq scripts/install_plugin.py
```

Restart PyMOL afterwards, so it picks up the new plugin.

### Step 5: Start the PyMOL Socket Listener

Before Claude can send commands to PyMOL, the socket listener must be active. Run this command to configure PyMOL to launch the plugin when the app opens.

```bash
make install-pymolrc
```

If `make` is not installed, create or edit `~/.pymolrc.py`.

```python
import importlib, threading, time

# PyMOL imports plugins from its startup directory under this name, so there is
# no path to configure -- it is identical on every machine and every PyMOL
# distribution. Requires the plugin to be installed (Step 4).
PLUGIN_MODULE = "pmg_tk.startup.pymol-mcp-socket-plugin"

def _auto_start_mcp_socket():
    time.sleep(3)  # let PyMOL's plugin system finish initializing
    try:
        plugin = importlib.import_module(PLUGIN_MODULE)
    except ImportError:
        print("MCP socket plugin not installed -- run: pymol -cq scripts/install_plugin.py")
        return
    try:
        # No port argument: claim the first free one, so a second PyMOL gets
        # its own listener rather than silently having none.
        if plugin.start_socket_server():
            print(f"MCP socket plugin auto-started on port {plugin.current_port}")
        else:
            print("MCP socket listener not started; every port in range is in use.")
    except Exception as e:
        print(f"MCP socket auto-start failed: {e}")

# Background thread so PyMOL startup isn't blocked
threading.Thread(target=_auto_start_mcp_socket, daemon=True).start()
```

## Usage

### Starting the Connection

With the socket listener running (Step 5):

- **Claude Desktop:** a hammer icon appears in the tools section when chatting;
  click it to access the PyMOL tools.
- **Claude Code (CLI):** start a new session in the terminal.

The MCP server also exposes `launch_pymol`, which opens a GUI, retains the
process handle, and waits until the new socket listener is discoverable. Clients
must obtain user approval before calling it because it opens a desktop window.
This is the preferred launch route in managed command environments; avoid
starting `pymol -q &` from a disposable shell, which may reap the background
process as soon as the shell exits.

### Example Commands

Here are some examples of what you can ask Claude to do:

- "Load PDB 1UBQ and display it as cartoon"
- "Color the protein by secondary structure"
- "Highlight the active site residues with sticks representation"
- "Align two structures and show their differences"
- "Calculate the distance between these two residues"
- "Save this view as a high-resolution image"

### Multiple PyMOL instances

Each PyMOL claims its own port, so you can run several and drive any of them.
Ask Claude to list them, then name the one you mean:

```
> list the PyMOL instances
  instance=9876, pid 4412: 1ubq
  instance=9877, pid 4488: 6vxx

> in 9877, colour chain A red
```

When more than one PyMOL instance is running, Claude must be directed to the
correct one.

### The PyMOL skill

`make install` also installs a skill from `skills/pymol-mcp/`, which gives
Claude Code and Codex higher-level guidance on driving this MCP server. To install it on
its own:

```bash
make install-skill
```

It goes into both `~/.claude/skills/` and Codex's `~/.codex/skills/`, so it
applies in any project directory. Start a new client session afterwards.

### Session history

Every command is written to disk as it runs, so a session survives PyMOL
closing. Two files in `~/.pymol-mcp/`:

| File | Contents |
|---|---|
| `history.jsonl` | Every MCP command with its arguments, outcome, and any error |
| `session-<timestamp>-<pid>.pml` | Validated state-changing commands, replayed from a clean state |

Replay a session, or reuse it as a figure script:

```bash
pymol -r ~/.pymol-mcp/session-20260722-114646-43120.pml
```

`load`, `save`, and `png` also record the absolute path they touched, since
PyMOL resolves a relative path against its own working directory.

The `get_history` tool reads all of this back without needing shell access to
the machine PyMOL is running on, filtered by command or to failures only.

Audit provenance and replay syntax are separate. Each JSONL record has a
`session_id`, `source` describing the MCP call, plus `replay` and `replayable`
when the call has valid PyMOL syntax. Composite operations may record a list of
replay lines. The PID in the session filename prevents concurrent PyMOL
instances from writing the same script. Read-only typed tools remain in the audit log
but never enter the `.pml`; typed state changes are rendered as real PyMOL
commands rather than Python dictionary strings. Every replay script starts with
`reinitialize`, and `load` paths are made absolute in the PyMOL process that
resolved them.

This deterministically reproduces MCP-controlled state. Changes made directly
in the GUI are outside the protocol and therefore cannot be replayed.

Export one session for replay, debugging or later workflow analysis with the
typed `export_session` tool:

```text
export_session(filename="/path/to/session.zip")
```

The ZIP contains `manifest.json`, session-filtered `history.jsonl`,
`replay.pml`, `artifacts.json`, and `final-state.json`. The artifact inventory
references input and output paths but does not copy molecular structures or
renders. A live-session export includes object, selection, camera and
representation evidence; a historical export records that no live-state
snapshot is available. Use `redact_paths=true` for a shareable analysis bundle.
Redaction deliberately makes its `replay.pml` non-executable.

Set `PYMOL_MCP_HISTORY=/some/dir` to write elsewhere, or `PYMOL_MCP_HISTORY=off`
to disable. The variable is read from the environment PyMOL was launched from.

## Troubleshooting

- **Connection issues**: Make sure the PyMOL plugin is listening before attempting to connect from Claude
- **Command errors**: Check the PyMOL output window for any error messages
- **`MCP socket plugin not installed`** on PyMOL startup, run
  `pymol -cq scripts/install_plugin.py`
- **`~/.pymolrc.py` is ignored**: PyMOL searches the working directory before
  `$HOME` and stops at the first directory holding a `pymolrc*` or `.pymolrc*`
  file, so launching from such a directory shadows your home config. To print
  the files PyMOL loads:

  ```bash
  pymol -cq -d "import pymol.invocation as i; print(i.get_user_config())"
  ```

- **Claude not connecting**: Verify the paths in your Claude configuration file are correct
- **Garbled client display**: PyMOL writes to the terminal it was launched from,
  which corrupts the display of a terminal client such as Claude Code. Launch
  PyMOL from its desktop icon or a separate terminal.
- **Server diagnostics**: The server logs nothing by default, because MCP clients
  treat a stdio server's stderr as an error stream and display every line. Set
  `PYMOL_MCP_LOG_LEVEL=INFO` (or `DEBUG`) in the server's `env` block to turn logging back on.

## Security

The listener binds to localhost and has no authentication, so any local process
can drive PyMOL through it.

`alter` and `alter_state` take expressions that PyMOL evaluates as Python. The
plugin parses those first and allows only arithmetic over atom properties,
rejecting attribute access, subscripting, lambdas and comprehensions.

## Contributing

Contributions are welcome. Please feel free to submit a Pull Request.

```
src/pymol_mcp/         MCP server and models; entry point `pymol-mcp`
pymol-mcp-socket-plugin/   PyMOL plugin (the directory name is the module
                           name PyMOL imports, so it cannot change)
scripts/               install_plugin, install_pymolrc, install_skill
shell/                 per-OS setup scripts that drive the above from a
                       freshly cloned repo
skills/pymol-mcp/      Claude Code skill
tests/                 pytest suite; conftest.py stubs the MCP framework
environment.yml        conda env for PyMOL; this repo's own deps are in
                       pyproject.toml, pinned by uv.lock
```

Run the test suite and linters with uv:

```bash
uv run pytest
uv run ruff check .
```

Or Make:

```bash
make test
make lint
```

## Credits

This project is derived from [vrtejus/pymol-mcp](https://github.com/vrtejus/pymol-mcp).

This repo is maintained by [Jonathan Ward](https://github.com/jonathan6620).
New features include an allowlisted command dispatcher, typed API, test suite,
multi-instance support, installation tooling, and usage skill.

## License

MIT. See the [LICENSE](LICENSE) file. Copyright is held jointly by the original
author and subsequent contributors; the original copyright notice is retained as
the license requires.
