# PyMOL-MCP: Integrating PyMOL with Claude AI

PyMOL-MCP connects PyMOL to Claude AI through the Model Context Protocol (MCP), enabling Claude to directly interact with and control PyMOL. This powerful integration allows for conversational structural biology, molecular visualization, and analysis through natural language.



https://github.com/user-attachments/assets/687f43dc-d45e-477e-ac2b-7438e175cb36



## Features

- **Two-way communication**: Connect Claude AI to PyMOL through a socket-based server
- **Intelligent command parsing**: Natural language processing for PyMOL commands
- **Molecular visualization control**: Manipulate representations, colors, and views
- **Structural analysis**: Perform measurements, alignments, and other analyses
- **Code execution**: Run arbitrary Python code in PyMOL from Claude

## Installation Guide

### Prerequisites

- PyMOL installed on your system
- Claude for Desktop
- Python 3.10 or newer
- Git

### Step 1: Install the UV Package Manager

**On macOS:**

```bash
brew install uv
```

**On Windows:**

```bash
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
set Path=C:\Users\[YourUsername]\.local\bin;%Path%
```

For other platforms, visit the [UV installation guide](https://docs.astral.sh/uv/getting-started/installation/).

### Step 2: Clone the Repository

```bash
git clone https://github.com/vrtejus/pymol-mcp
cd pymol-mcp
```

### Step 3: Set Up the Environment

Create and activate a Python virtual environment:

```bash
python -m venv venv
```

**On macOS/Linux:**

```bash
source venv/bin/activate
```

**On Windows:**

```bash
venv\Scripts\activate
```

### Step 4: Install Dependencies

With the virtual environment activated:

```bash
pip install mcp
```

### Step 5: Configure Claude

#### Option A: Claude Desktop

1. Open Claude Desktop
2. Go to Claude > Settings > Developer > Edit Config
3. This will open the `claude_desktop_config.json` file
4. Add the MCP server configuration:

```json
{
  "mcpServers": {
    "pymol": {
      "command": "[Full path to your venv python]",
      "args": ["[Full path to pymol_mcp_server.py]"]
    }
  }
}
```

For example:

```json
{
  "mcpServers": {
    "pymol": {
      "command": "/Users/username/pymol-mcp/venv/bin/python",
      "args": ["/Users/username/pymol-mcp/pymol_mcp_server.py"]
    }
  }
}
```

> **Note:** Use the actual full paths on your system. On Windows, use forward slashes (/) instead of backslashes.

#### Option B: Claude Code (CLI)

Add the PyMOL MCP server using the `claude` CLI:

```bash
claude mcp add pymol -s user -- /path/to/pymol-mcp/venv/bin/python /path/to/pymol-mcp/pymol_mcp_server.py
```

For example:

```bash
claude mcp add pymol -s user -- /home/username/pymol-mcp/venv/bin/python /home/username/pymol-mcp/pymol_mcp_server.py
```

This saves the configuration to `~/.claude.json`. You can verify it was added with:

```bash
claude mcp list
```

> **Note:** After adding the MCP server, you must restart your Claude Code session for the tools to become available.

### Step 6: Install the PyMOL Socket Plugin

The MCP server communicates with PyMOL through a socket connection on port 9876. You need to install the socket listener plugin in PyMOL:

1. Open PyMOL
2. Go to **Plugin > Plugin Manager**
3. Click on the **"Install New Plugin"** tab
4. Select **"Choose file..."** and navigate to the cloned repository
5. Select the `pymol-mcp-socket-plugin/__init__.py` file
6. Click **"Open"** and follow the prompts to install the plugin
7. Restart PyMOL after installation

### Step 7: Start the PyMOL Socket Listener

Before Claude can send commands to PyMOL, the socket listener must be active. There are two options:

#### Option A: Manual start (via GUI)

1. In PyMOL, go to **Plugin > PyMol MCP Socket Plugin**
2. A dialog will appear with a port number (default: **9876**) and a "Start Listening" button
3. Click **"Start Listening"**
4. The status label should turn green and read **"Listening on port 9876"**

> **Note:** The manual method requires clicking "Start Listening" each time you open PyMOL.

#### Option B: Auto-start on PyMOL launch (via `.pymolrc.py`)

Create or edit `~/.pymolrc.py` to auto-start the socket listener whenever PyMOL opens. You need to adjust `plugin_paths` to match where PyMOL installed the plugin on your system:

```python
import threading
import time

def _auto_start_mcp_socket():
    """Wait for PyMOL to finish loading, then start the MCP socket plugin."""
    time.sleep(3)  # wait for plugin system to initialize
    try:
        import importlib.util
        import glob

        # Find the installed plugin.
        # Adjust this glob to match your PyMOL installation path:
        #   Linux (conda): ~/miniconda/envs/<env>/lib/python3.*/site-packages/pmg_tk/startup/
        #   macOS (app):   /Applications/PyMOL.app/.../pmg_tk/startup/
        #   Linux (pip):   ~/.local/lib/python3.*/site-packages/pmg_tk/startup/
        plugin_paths = glob.glob(
            "*/lib/python3.*/site-packages/"
            "pmg_tk/startup/pymol-mcp-socket-plugin/__init__.py"
        )
        if not plugin_paths:
            print("MCP socket plugin not found")
            return

        spec = importlib.util.spec_from_file_location(
            "pymol_mcp_socket_plugin", plugin_paths[0]
        )
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)

        from pymol import cmd

        if not plugin.listening:
            dispatcher = plugin.build_command_dispatcher(cmd)

            def execute_structured_command(command_name, args):
                import io, traceback
                from contextlib import redirect_stdout
                try:
                    handler = dispatcher.get(command_name)
                    if handler is None:
                        return {"executed": False, "error": f"Unknown command: {command_name}"}
                    output_buffer = io.StringIO()
                    with redirect_stdout(output_buffer):
                        result = handler(args)
                    output = output_buffer.getvalue()
                    if output:
                        return {"executed": True, "output": output}
                    elif result is not None:
                        return {"executed": True, "output": str(result)}
                    else:
                        return {"executed": True, "output": "Command executed successfully (no output)"}
                except Exception as e:
                    traceback.print_exc()
                    return {"executed": False, "error": str(e)}

            server = plugin.SocketServer(port=9876)
            if server.start(execute_structured_command):
                plugin.socket_server = server
                plugin.listening = True
                print("MCP socket plugin auto-started on port 9876")
    except Exception as e:
        print(f"MCP socket auto-start failed: {e}")

# Start in background thread so it doesn't block PyMOL startup
threading.Thread(target=_auto_start_mcp_socket, daemon=True).start()
```

With this in place, the socket listener starts automatically every time you launch PyMOL.

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

1. In PyMOL:

   - Go to Plugin > PyMOL MCP Socket Plugin
   - Click "Start Listening"
   - The status should change to "Listening on port 9876"

2. In Claude Desktop:
   - You should see a hammer icon in the tools section when chatting
   - Click it to access the PyMOL tools

3. In Claude Code (CLI):
   - Start a new session after configuring the MCP server
   - The `parse_and_execute` tool will be available automatically
   - Ask Claude to load structures, change representations, etc.

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
- Join our Bio-MCP Community to troubleshoot, provide feedback & improve Bio-MCPS! https://join.slack.com/t/bio-mcpslack/shared_invite/zt-31z4pho39-K5tb6sZ1hUvrFyoPmKihAA

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
