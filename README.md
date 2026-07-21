# PyMOL-MCP: Integrating PyMOL with Claude AI

PyMOL-MCP connects PyMOL to Claude AI through the Model Context Protocol (MCP), enabling Claude to directly interact with and control PyMOL. This powerful integration allows for conversational structural biology, molecular visualization, and analysis through natural language.

> Originally created by [Vishnu Rajan Tejus](https://github.com/vrtejus) as
> [vrtejus/pymol-mcp](https://github.com/vrtejus/pymol-mcp). This is a continuation
> of that work — see [Credits](#credits).



https://github.com/user-attachments/assets/687f43dc-d45e-477e-ac2b-7438e175cb36



## Features

- **Two-way communication**: Connect Claude AI to PyMOL through a socket-based server
- **Intelligent command parsing**: Natural language processing for PyMOL commands
- **Molecular visualization control**: Manipulate representations, colors, and views
- **Structural analysis**: Perform measurements, alignments, and other analyses
- **No arbitrary code execution**: Only allowlisted `cmd.*` calls are dispatched — no `exec()` or `eval()`

## Quick Start with Make

For Claude Code, with [uv](#step-1-install-the-uv-package-manager) and PyMOL
already installed:

```bash
git clone https://github.com/jonathan6620/pymol-mcp
cd pymol-mcp
uv sync
claude mcp add pymol -s user -- uv --directory $(pwd) run --quiet pymol_mcp_server.py
make install
```

Then restart PyMOL and start a new Claude Code session. PyMOL prints
`MCP socket plugin auto-started on port 9876` on startup once it is working.

`make install` covers [Step 4](#step-4-install-the-pymol-socket-plugin) and
[Step 5](#step-5-start-the-pymol-socket-listener): it symlinks the socket plugin
into PyMOL's plugin directory and adds the auto-start block to `~/.pymolrc.py`.
Both halves are idempotent, so rerunning is safe — and rerun after a `git pull`
if symlinks weren't available on your system.

| Target | What it does |
| --- | --- |
| `make install` | Both steps below |
| `make install-plugin` | Symlink the socket plugin into PyMOL's plugin directory |
| `make install-pymolrc` | Add the auto-start block to `~/.pymolrc.py` |
| `make test` | Run the test suite |
| `make lint` | Run ruff |
| `make help` | List targets, and show which PyMOL was detected |

Two things it may ask of you:

- **`error: could not find a pymol executable`** — pass the path:
  `make install PYMOL=/full/path/to/pymol`. A shell *alias* for `pymol` will not
  do, because `make` runs recipes with `/bin/sh`, which does not read aliases; in
  zsh, `which pymol` prints the path an alias points at.
- **`already contains an auto-start snippet that this script does not manage`** —
  you have an older hand-pasted snippet in `~/.pymolrc.py`. Delete it and rerun,
  or use `make install FORCE=1` to replace the file (a timestamped backup is kept
  either way).

Using **Claude Desktop** instead? It's configured through a JSON file rather than
the `claude` CLI — replace the `claude mcp add` line above with
[Step 3, Option A](#option-a-claude-desktop), then run `make install` as shown.

The rest of this guide explains each step in full, including manual alternatives
to every `make` target.

## Installation Guide

### Prerequisites

- PyMOL installed on your system
- Claude for Desktop
- Git

(uv manages the Python toolchain itself, so you don't need a system Python 3.10+.)

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

`uv sync` creates `.venv` and installs everything pinned in `uv.lock` —
including a suitable Python if you don't have one. There's no separate
dependency-install step, and no virtualenv to activate: `uv run` always uses
`.venv`.

### Step 3: Configure Claude

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
        "--quiet",
        "pymol_mcp_server.py"
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
        "--quiet",
        "pymol_mcp_server.py"
      ]
    }
  }
}
```

> **Note:** Use the actual full paths on your system — run `which uv` (macOS/Linux)
> or `where uv` (Windows) to find the uv binary, since Claude Desktop does not
> inherit your shell's `PATH`. On Windows, use forward slashes (/) instead of
> backslashes.

> **Note:** `--quiet` keeps uv's own progress and lockfile messages off stderr.
> MCP clients report anything a stdio server writes to stderr as a server error,
> so without it uv's chatter shows up as errors in the client UI.

#### Option B: Claude Code (CLI)

From the cloned repository directory, add the PyMOL MCP server using the `claude` CLI:

```bash
claude mcp add pymol -s user -- uv --directory $(pwd) run --quiet pymol_mcp_server.py
```

`$(pwd)` expands to the repo you're standing in, so run this from the `pymol-mcp`
directory you cloned in Step 2. From anywhere else, pass the full path instead:

```bash
claude mcp add pymol -s user -- uv --directory /path/to/pymol-mcp run --quiet pymol_mcp_server.py
```

This saves the configuration to `~/.claude.json`. You can verify it was added with:

```bash
claude mcp list
```

> **Note:** After adding the MCP server, you must restart your Claude Code session for the tools to become available.

### Step 4: Install the PyMOL Socket Plugin

The MCP server communicates with PyMOL through a socket connection on port 9876.
Install the socket listener plugin from the repository you cloned in Step 2:

```bash
pymol -cq install_plugin.py
```

> **Shortcut:** `make install` does this step *and* Step 5's auto-start
> configuration in one go. It finds PyMOL on `PATH` or in the usual conda /
> `PyMOL.app` locations — `make help` prints which one it picked. If it comes up
> empty, pass the path explicitly:
> `make install PYMOL=~/miniconda3/envs/pymol-env/bin/pymol`.
>
> A shell **alias** for `pymol` will not help here: `make` runs its recipes with
> `/bin/sh`, which does not read shell aliases, so `pymol` can work when you type
> it and still be invisible to `make`. In zsh, `which pymol` prints the path an
> alias points at.

Restart PyMOL afterwards. That's the whole step on every platform — the script
runs inside PyMOL and asks PyMOL where its plugin directory is, so there is no
path to look up or edit.

It prints where the plugin landed, then imports it back the same way PyMOL does
to confirm it actually loads:

```
Installed: /path/to/site-packages/pmg_tk/startup/pymol-mcp-socket-plugin
  -> symlink to /path/to/pymol-mcp/pymol-mcp-socket-plugin
Plugin verified. `git pull` now updates PyMOL's plugin automatically.
```

If `pymol` isn't on your `PATH`, use the full path to the binary (conda
installs put it in `<env>/bin/pymol`; on Windows, `<env>\Scripts\pymol.exe`).

> **Why not the Plugin Manager?** *Plugin > Plugin Manager > Install New Plugin*
> works, but it installs a **copy**. That copy does not change when you
> `git pull`, so the repo and the plugin PyMOL actually loads drift apart
> silently — and the resulting failures point nowhere near the real cause (an
> older copy predates `start_socket_server`, so auto-start reports the plugin as
> *missing* even though it is installed). The script symlinks instead, so the
> plugin PyMOL loads is always this checkout.
>
> Where symlinks aren't available (Windows without Developer Mode) it falls back
> to a copy and tells you so; rerun it after each `git pull` in that case.

To upgrade an existing install — including one made through the Plugin Manager —
just run the same command. It replaces whatever is there and is safe to rerun.

### Step 5: Start the PyMOL Socket Listener

Before Claude can send commands to PyMOL, the socket listener must be active. There are two options:

#### Option A: Manual start (via GUI)

1. In PyMOL, go to **Plugin > PyMol MCP Socket Plugin**
2. A dialog will appear with a port number (default: **9876**) and a "Start Listening" button
3. Click **"Start Listening"**
4. The status label should turn green and read **"Listening on port 9876"**

> **Note:** The manual method requires clicking "Start Listening" each time you open PyMOL.

#### Option B: Auto-start on PyMOL launch (via `.pymolrc.py`)

Run this to configure it for you:

```bash
make install-pymolrc
```

It writes the snippet below into `~/.pymolrc.py` between `# >>> pymol-mcp
auto-start >>>` markers, so rerunning it updates that block and leaves the rest
of your file alone. It backs the file up first, and refuses to append a second
copy if you already pasted the snippet by hand — remove the old one, or pass
`FORCE=1` to replace the file outright.

To do it by hand instead, create or edit `~/.pymolrc.py` so the listener starts
every time PyMOL opens:

```python
import importlib, threading, time

# PyMOL imports plugins from its startup directory under this name, so there is
# no path to configure -- it is identical on every machine and every PyMOL
# distribution. Requires the plugin to be installed (Step 4).
PLUGIN_MODULE = "pmg_tk.startup.pymol-mcp-socket-plugin"
PORT = 9876

def _auto_start_mcp_socket():
    time.sleep(3)  # let PyMOL's plugin system finish initializing
    try:
        plugin = importlib.import_module(PLUGIN_MODULE)
    except ImportError:
        print("MCP socket plugin not installed -- run: pymol -cq install_plugin.py")
        return
    try:
        if plugin.start_socket_server(PORT):
            print(f"MCP socket plugin auto-started on port {PORT}")
        else:
            # Already listening, or the port is taken -- often another PyMOL
            # instance still holding it.
            print(f"MCP socket listener not started; is port {PORT} already in use?")
    except Exception as e:
        print(f"MCP socket auto-start failed: {e}")

# Background thread so PyMOL startup isn't blocked
threading.Thread(target=_auto_start_mcp_socket, daemon=True).start()
```

Opening the plugin dialog later still works — it shows the already-running
listener, and "Stop Listening" stops it. This works because `import_module`
returns the *same* module object PyMOL's own plugin manager loaded, so the
dialog and the auto-start share one listener. (Loading the file by path
instead — via `spec_from_file_location` — builds a second, independent copy of
the module: the listener runs in one copy while the dialog reads the other's
state and reports "Not listening".)

#### Don't launch PyMOL from the terminal running your MCP client

PyMOL writes to the terminal it was started from. If that is the same terminal
a terminal-UI client such as Claude Code is drawing in, PyMOL's output
interleaves with the client's screen drawing and garbles the display. Start
PyMOL from its desktop launcher, from a separate terminal, or detached:

```bash
pymol structure.cif >/dev/null 2>&1 &
```

This plugin keeps quiet by default for the same reason; set `PYMOL_MCP_VERBOSE=1`
in PyMOL's environment to get per-command tracing back. PyMOL's own messages are
not affected by that flag, so the separate-terminal rule still applies.

#### Verifying the socket is active

You can confirm the listener is running from a terminal:

```bash
# Linux
ss -tlnp | grep 9876

# macOS
lsof -i :9876
```

## Usage

### Starting the Connection

With the socket listener running (Step 5):

- **Claude Desktop:** a hammer icon appears in the tools section when chatting —
  click it to access the PyMOL tools.
- **Claude Code (CLI):** start a new session; the `parse_and_execute` tool is
  available automatically.

### Example Commands

Here are some examples of what you can ask Claude to do:

- "Load PDB 1UBQ and display it as cartoon"
- "Color the protein by secondary structure"
- "Highlight the active site residues with sticks representation"
- "Align two structures and show their differences"
- "Calculate the distance between these two residues"
- "Save this view as a high-resolution image"

## Troubleshooting

- **Connection issues**: Make sure the PyMOL plugin is listening before attempting to connect from Claude
- **Command errors**: Check the PyMOL output window for any error messages
- **`MCP socket plugin not installed`** on PyMOL startup: the plugin isn't in
  PyMOL's startup directory. Run `pymol -cq install_plugin.py` (Step 4). If you
  installed it through the Plugin Manager and it still fails, you likely have a
  stale copy from an older checkout — the install script replaces it.
- **`AttributeError: no attribute 'start_socket_server'`**: an outdated copy of
  the plugin. This is the pre-symlink failure mode: the Plugin Manager's copy
  predates that function, so PyMOL loads it and auto-start cannot find the entry
  point. Rerun `pymol -cq install_plugin.py`.
- **Dialog says "Not listening" while the port is clearly in use**: your
  `~/.pymolrc.py` is loading the plugin by file path rather than importing
  `pmg_tk.startup.pymol-mcp-socket-plugin`, so the dialog and the listener are
  looking at two different copies of the module. Use the snippet in
  [Step 5 Option B](#option-b-auto-start-on-pymol-launch-via-pymolrcpy).
- **`~/.pymolrc.py` seems to be ignored**: PyMOL searches the *current working
  directory* before `$HOME` and stops at the first directory containing any
  `pymolrc*` or `.pymolrc*` file. Launching PyMOL from a directory that has one
  therefore shadows your home config entirely. `pymol -cq -d "import pymol.invocation
  as i; print(i.get_user_config())"` prints which files it will actually load.
- **Plugin not appearing**: Restart PyMOL and check that the plugin was correctly installed
- **Claude not connecting**: Verify the paths in your Claude configuration file are correct
- **Garbled client display**: PyMOL was almost certainly launched from the same
  terminal as your MCP client — see
  [Don't launch PyMOL from the terminal running your MCP client](#dont-launch-pymol-from-the-terminal-running-your-mcp-client)
- **Server diagnostics**: The server logs nothing by default, because MCP clients
  treat a stdio server's stderr as an error stream and display every line. Set
  `PYMOL_MCP_LOG_LEVEL=INFO` (or `DEBUG`) in the server's `env` block to turn
  logging back on; unset it once you're done.

## Limitations & Notes

- The socket connection requires both PyMOL and Claude to be running on the same machine
- Some complex operations may need to be broken down into simpler steps
- Always save your work before using experimental features
- The upstream project runs a Bio-MCP Slack community for troubleshooting and feedback on Bio-MCPs: https://join.slack.com/t/bio-mcpslack/shared_invite/zt-31z4pho39-K5tb6sZ1hUvrFyoPmKihAA

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

Run the test suite and linters with uv:

```bash
uv run pytest
uv run ruff check .
```

## Credits

PyMOL-MCP was created by **[Vishnu Rajan Tejus](https://github.com/vrtejus)** at
[vrtejus/pymol-mcp](https://github.com/vrtejus/pymol-mcp), with a documentation
contribution from [Dimple Amitha Garuadapuri](https://github.com/AmithaGaruadapuri).
The socket plugin, the MCP server, and the original PyMOL command bridge are all
their work.

This repository continues that project independently rather than as a GitHub
fork, so it can diverge freely. The full upstream commit history is preserved
here — `git log` shows the original authorship. Changes since the fork point:

- Replaced `exec()` with an allowlisted command dispatcher
- Added Pydantic models, type hints, and a test suite
- Reworked setup around uv and added Claude Code CLI instructions

## License

MIT — see the [LICENSE](LICENSE) file. Copyright is held jointly by the original
author and subsequent contributors; the original copyright notice is retained as
the license requires.
