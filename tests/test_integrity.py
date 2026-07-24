"""Server/plugin consistency and real-import checks."""

import re
import sys

import pytest
from conftest import PLUGIN_PATH, REPO_ROOT

from pymol_mcp.server import (
    PYMOL_COMMANDS,
)

# ============================================================================
# 8. SERVER <-> PLUGIN SYNC
# ============================================================================


class TestServerPluginSync:
    """Check that MCP server command definitions stay in sync with the
    socket plugin's dispatcher allowlist."""

    @pytest.fixture
    def plugin_dispatcher_commands(self):
        """Parse the plugin __init__.py to extract dispatcher keys."""
        plugin_path = PLUGIN_PATH
        with open(plugin_path) as f:
            source = f.read()

        commands = set()

        # Extract dispatcher dict keys
        for match in re.finditer(r'"(\w[\w.]*)":\s*_\w+', source):
            commands.add(match.group(1))

        # Extract util_commands list entries
        for match in re.finditer(r'"(util\.\w+)"', source):
            commands.add(match.group(1))

        return commands

    def test_server_commands_exist_in_plugin(self, plugin_dispatcher_commands):
        """Every non-composite server command should have a plugin handler."""
        missing = []
        for cmd_name, cmd_info in PYMOL_COMMANDS.items():
            if cmd_info.composite:
                continue  # composite commands are handled server-side
            if cmd_name not in plugin_dispatcher_commands:
                missing.append(cmd_name)
        if missing:
            pytest.fail(
                f"Commands in MCP server but NOT in plugin dispatcher: {missing}"
            )

    def test_plugin_commands_exist_in_server(self, plugin_dispatcher_commands):
        """Every plugin dispatcher command should be defined in the server."""
        server_commands = set(PYMOL_COMMANDS.keys())
        extra = []
        for cmd in plugin_dispatcher_commands:
            if cmd not in server_commands:
                extra.append(cmd)
        if extra:
            pytest.fail(
                f"Commands in plugin dispatcher but NOT in MCP server: {extra}"
            )

    def test_plugin_util_commands_not_deprecated(self, plugin_dispatcher_commands):
        """Flag util commands in plugin that are known deprecated/broken."""
        deprecated = {"util.ss", "util.color_secondary"}
        present = deprecated & plugin_dispatcher_commands
        if present:
            pytest.fail(
                f"Deprecated/broken util commands still in plugin dispatcher: {present}"
            )


# ============================================================================
# 15. SERVER MODULE INTEGRITY
# ============================================================================


class TestServerModuleIntegrity:
    """Verify the MCP server module loads correctly and is properly configured.

    These tests catch issues like incompatible keyword arguments in FastMCP
    (e.g. 'description' vs 'instructions') that prevent the server from starting.
    """

    def test_server_module_imports_without_mock(self):
        """The server module should import cleanly with the real mcp package."""
        import subprocess
        result = subprocess.run(
            [
                sys.executable, "-c",
                "from pymol_mcp.server import PYMOL_COMMANDS, ERROR_PATTERNS, "
                "parse_pymol_input, analyze_pymol_output; "
                "print('imports_ok')"
            ],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"Server module failed to import:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "imports_ok" in result.stdout

    def test_fastmcp_instantiation(self):
        """FastMCP should instantiate without TypeError on keyword args.

        Catches the description->instructions rename in mcp>=1.1.0.
        """
        import subprocess
        result = subprocess.run(
            [
                sys.executable, "-c",
                "from pymol_mcp.server import mcp; "
                "print(f'name={mcp.name}')"
            ],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"FastMCP instantiation failed:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "name=PyMOLMCPServer" in result.stdout

    def test_fastmcp_does_not_accept_description_kwarg(self):
        """Verify that 'description' is NOT a valid FastMCP kwarg (it was renamed).

        This documents the API change so the wrong kwarg isn't reintroduced.
        Uses subprocess to avoid the mocked mcp module in this test file.
        """
        import subprocess
        result = subprocess.run(
            [
                sys.executable, "-c",
                "from mcp.server.fastmcp import FastMCP; "
                "import inspect; "
                "sig = inspect.signature(FastMCP.__init__); "
                "print('description' not in sig.parameters)"
            ],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, f"Failed: {result.stderr}"
        assert "True" in result.stdout, (
            "FastMCP.__init__ unexpectedly accepts 'description'. "
            "If the mcp package restored this kwarg, update "
            "src/pymol_mcp/server.py accordingly."
        )

    def test_fastmcp_accepts_instructions_kwarg(self):
        """Verify that 'instructions' is the correct FastMCP kwarg.

        Uses subprocess to avoid the mocked mcp module in this test file.
        """
        import subprocess
        result = subprocess.run(
            [
                sys.executable, "-c",
                "from mcp.server.fastmcp import FastMCP; "
                "import inspect; "
                "sig = inspect.signature(FastMCP.__init__); "
                "print('instructions' in sig.parameters)"
            ],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, f"Failed: {result.stderr}"
        assert "True" in result.stdout, (
            "FastMCP.__init__ does not accept 'instructions'. "
            "The mcp package API may have changed again."
        )

    def test_render_png_does_not_declare_structured_output(self):
        """render_png must not build a pydantic output model.

        It returns [str, Image]. When FastMCP derives an output model from the
        return annotation it serialises the result through pydantic, which
        cannot encode an Image, and every call fails with
        PydanticSerializationError. Annotating the return as list[ContentBlock]
        does not help either -- the str and Image are then rejected by output
        validation instead. The fix is structured_output=False on the decorator.

        Subprocess, because conftest replaces the whole mcp package with a
        MagicMock in-process, so the decorator's real behaviour is invisible to
        every other test in the suite.
        """
        import subprocess
        result = subprocess.run(
            [
                sys.executable, "-c",
                "from pymol_mcp.server import mcp; "
                "tool = mcp._tool_manager.get_tool('render_png'); "
                "print(f'output_schema={tool.fn_metadata.output_schema}')"
            ],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"Failed to inspect render_png:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "output_schema=None" in result.stdout, (
            "render_png declares a structured output schema. Returning an Image "
            "through it raises PydanticSerializationError at call time. Keep "
            "structured_output=False on the @mcp.tool decorator."
        )

    def test_render_png_result_converts_to_image_content(self, tmp_path):
        """A [str, Image] return must reach the client as text + image blocks."""
        import struct
        import subprocess
        import zlib

        def chunk(kind: bytes, data: bytes) -> bytes:
            checksum = zlib.crc32(kind)
            checksum = zlib.crc32(data, checksum) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

        png_path = tmp_path / "render.png"
        png_path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 640, 480, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", b"")
            + chunk(b"IEND", b"")
        )

        result = subprocess.run(
            [
                sys.executable, "-c",
                "import sys; "
                "from pymol_mcp.server import mcp; "
                "from mcp.server.fastmcp.utilities.types import Image; "
                "tool = mcp._tool_manager.get_tool('render_png'); "
                "blocks = tool.fn_metadata.convert_result("
                "    ['Rendered render.png (640x480, 300 DPI, ray=on)', "
                "     Image(path=sys.argv[1])]); "
                "print('kinds=' + ','.join(type(b).__name__ for b in blocks)); "
                "print('mime=' + blocks[1].mimeType); "
                "print('has_data=' + str(bool(blocks[1].data)))",
                str(png_path),
            ],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"render_png result conversion failed:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "kinds=TextContent,ImageContent" in result.stdout
        assert "mime=image/png" in result.stdout
        assert "has_data=True" in result.stdout

    def test_parse_and_execute_tool_registered(self):
        """The parse_and_execute function should be registered as an MCP tool."""
        import subprocess
        result = subprocess.run(
            [
                sys.executable, "-c",
                "from pymol_mcp.server import mcp; "
                "tools = [t.name for t in mcp._tool_manager.list_tools()]; "
                "print(f'tools={tools}')"
            ],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"Failed to list tools:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "parse_and_execute" in result.stdout, (
            f"parse_and_execute not registered as a tool. Output: {result.stdout}"
        )


# ============================================================================
# 16. SOCKET CONNECTION CONFIGURATION
# ============================================================================


class TestSocketConnectionDefaults:
    """Verify socket connection defaults match between server and plugin."""

    def test_default_port_is_9876(self):
        """Both server and plugin should use port 9876 by default."""
        import subprocess
        result = subprocess.run(
            [
                sys.executable, "-c",
                "from pymol_mcp.server import PyMOLConnection; "
                "conn = PyMOLConnection(); "
                "print(f'host={conn.host} port={conn.port}')"
            ],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, f"Import failed: {result.stderr}"
        assert "port=9876" in result.stdout
        assert "host=localhost" in result.stdout

    def test_plugin_default_port_matches_server(self):
        """The plugin's default port should match the server's default."""
        plugin_path = PLUGIN_PATH
        with open(plugin_path) as f:
            source = f.read()
        assert "current_port = 9876" in source, (
            "Plugin default port is not 9876 — server and plugin are out of sync"
        )


# ============================================================================
# 17. PYDANTIC MODEL VALIDATION
# ============================================================================
