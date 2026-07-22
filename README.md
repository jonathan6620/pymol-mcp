# PyMOL-MCP: Integrating PyMOL with Claude AI

PyMOL-MCP connects PyMOL to Claude AI through the Model Context Protocol (MCP), enabling Claude to directly interact with and control PyMOL. This powerful integration allows for conversational structural biology, molecular visualization, and analysis through natural language.

> Derived from [vrtejus/pymol-mcp](https://github.com/vrtejus/pymol-mcp) by
> [Vishnu Rajan Tejus](https://github.com/vrtejus). See [Credits](#credits).


## Features

- **Two-way communication**: Connect Claude AI to PyMOL through a socket-based server
- **Intelligent command parsing**: Natural language processing for PyMOL commands
- **Molecular visualization control**: Manipulate representations, colors, and views
- **Structural analysis**: Perform measurements, alignments, and other analyses
- **No arbitrary code execution**: Only allowlisted `cmd.*` calls are dispatched, with no `exec()` or `eval()`

## Prerequisites

- PyMOL installed on your system
- Claude Desktop or Claude Code
- Git
- Make, if you want to use the [Quick Start](#quick-start)

## Quick Start

For Claude Code, with [uv](#step-1-install-the-uv-package-manager), *PyMOL* and *Make* installed:

```bash
git clone https://github.com/jonathan6620/pymol-mcp
cd pymol-mcp
uv sync
claude mcp add pymol -s user -- uv --directory $(pwd) run --quiet pymol_mcp_server.py
make install
```

Restart PyMOL and start a new Claude Code session. On startup PyMOL prints
`MCP socket plugin auto-started on port 9876`.

If `make` cannot find the PyMOL executable, then pass the path:
`make install PYMOL=/full/path/to/pymol`.

For Claude Desktop, use [Step 3, Option A](#option-a-claude-desktop) in place of
the `claude mcp add` line, then run `make install`.

## Full Installation Guide

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

### Step 3: Configure Claude

This can use either the Desktop App or Claude Code.

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

> **Note:** Ensure that you specify the full paths for your system. Run `which uv` on macOS/Linux
> or `where uv` (Windows) to find the uv binary, since Claude Desktop does not
> inherit your shell's `PATH`. On Windows, use forward slashes (/) instead of
> backslashes.

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

The MCP server communicates with PyMOL through a socket connection on port 9876. Install the socket listener plugin from the repository you cloned in Step 2:

```bash
pymol -cq install_plugin.py
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

- **Claude Desktop:** a hammer icon appears in the tools section when chatting;
  click it to access the PyMOL tools.
- **Claude Code (CLI):** start a new session in the terminal.

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
- **`MCP socket plugin not installed`** on PyMOL startup, run
  `pymol -cq install_plugin.py`
- **Dialog says "Not listening" while the port is in use**: your `~/.pymolrc.py`
  loads the plugin by file path, giving the dialog and the listener separate
  copies of the module. Use the snippet in
  [Step 5](#step-5-start-the-pymol-socket-listener).
- **`~/.pymolrc.py` is ignored**: PyMOL searches the working directory before
  `$HOME` and stops at the first directory holding a `pymolrc*` or `.pymolrc*`
  file, so launching from such a directory shadows your home config. To print
  the files PyMOL loads:

  ```bash
  pymol -cq -d "import pymol.invocation as i; print(i.get_user_config())"
  ```

- **Plugin not appearing**: Restart PyMOL and check that the plugin was correctly installed
- **Claude not connecting**: Verify the paths in your Claude configuration file are correct
- **Garbled client display**: PyMOL writes to the terminal it was launched from,
  which corrupts the display of a terminal client such as Claude Code. Launch
  PyMOL from its desktop icon or a separate terminal.
- **Server diagnostics**: The server logs nothing by default, because MCP clients
  treat a stdio server's stderr as an error stream and display every line. Set
  `PYMOL_MCP_LOG_LEVEL=INFO` (or `DEBUG`) in the server's `env` block to turn logging back on.

## Limitations & Notes

- The socket connection requires both PyMOL and Claude to be running on the same machine
- Some complex operations may need to be broken down into simpler steps
- Always save your work before using experimental features

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

Run the test suite and linters with uv:

```bash
uv run pytest
uv run ruff check .
```

Or Make:

```bash
make test
```

## Credits

Derived from [vrtejus/pymol-mcp](https://github.com/vrtejus/pymol-mcp) by
[Vishnu Rajan Tejus](https://github.com/vrtejus). The socket transport and the
Qt dialog are largely unchanged from that project.

This repository continues it independently rather than as a GitHub fork, so it
can diverge freely. The full upstream commit history is preserved here; `git log`
shows the original authorship. Changes since the fork point:

- Replaced `exec()` with an allowlisted command dispatcher
- Added Pydantic models, type hints, and a test suite
- Reworked setup around uv and added Claude Code CLI instructions

## License

MIT. See the [LICENSE](LICENSE) file. Copyright is held jointly by the original
author and subsequent contributors; the original copyright notice is retained as
the license requires.
