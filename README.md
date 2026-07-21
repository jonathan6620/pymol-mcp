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

#### Option B: Claude Code (CLI)

Add the PyMOL MCP server using the `claude` CLI:

```bash
claude mcp add pymol -s user -- uv --directory /path/to/pymol-mcp run pymol_mcp_server.py
```

For example:

```bash
claude mcp add pymol -s user -- uv --directory /home/username/pymol-mcp run pymol_mcp_server.py
```

This saves the configuration to `~/.claude.json`. You can verify it was added with:

```bash
claude mcp list
```

> **Note:** After adding the MCP server, you must restart your Claude Code session for the tools to become available.

### Step 4: Install the PyMOL Socket Plugin

The MCP server communicates with PyMOL through a socket connection on port 9876. You need to install the socket listener plugin in PyMOL:

1. Open PyMOL
2. Go to **Plugin > Plugin Manager**
3. Click on the **"Install New Plugin"** tab
4. Select **"Choose file..."** and navigate to the cloned repository
5. Select the `pymol-mcp-socket-plugin/__init__.py` file
6. Click **"Open"** and follow the prompts to install the plugin
7. Restart PyMOL after installation

### Step 5: Start the PyMOL Socket Listener

Before Claude can send commands to PyMOL, the socket listener must be active. There are two options:

#### Option A: Manual start (via GUI)

1. In PyMOL, go to **Plugin > PyMol MCP Socket Plugin**
2. A dialog will appear with a port number (default: **9876**) and a "Start Listening" button
3. Click **"Start Listening"**
4. The status label should turn green and read **"Listening on port 9876"**

> **Note:** The manual method requires clicking "Start Listening" each time you open PyMOL.

#### Option B: Auto-start on PyMOL launch (via `.pymolrc.py`)

Create or edit `~/.pymolrc.py` so the listener starts every time PyMOL opens:

```python
import glob, importlib.util, threading, time

# Where PyMOL installed the plugin. Adjust to match your installation:
#   Linux (conda): ~/miniconda/envs/<env>/lib/python3.*/site-packages/pmg_tk/startup/
#   Linux (pip):   ~/.local/lib/python3.*/site-packages/pmg_tk/startup/
#   macOS (app):   /Applications/PyMOL.app/.../pmg_tk/startup/
PLUGIN_GLOB = "*/lib/python3.*/site-packages/pmg_tk/startup/pymol-mcp-socket-plugin/__init__.py"

def _auto_start_mcp_socket():
    time.sleep(3)  # let the plugin system finish initializing
    try:
        paths = glob.glob(PLUGIN_GLOB)
        if not paths:
            print("MCP socket plugin not found")
            return
        spec = importlib.util.spec_from_file_location("pymol_mcp_socket_plugin", paths[0])
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        if plugin.start_socket_server(9876):
            print("MCP socket plugin auto-started on port 9876")
    except Exception as e:
        print(f"MCP socket auto-start failed: {e}")

# Background thread so PyMOL startup isn't blocked
threading.Thread(target=_auto_start_mcp_socket, daemon=True).start()
```

Opening the plugin dialog later still works — it shows the already-running
listener, and "Stop Listening" stops it.

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
- **Plugin not appearing**: Restart PyMOL and check that the plugin was correctly installed
- **Claude not connecting**: Verify the paths in your Claude configuration file are correct

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
