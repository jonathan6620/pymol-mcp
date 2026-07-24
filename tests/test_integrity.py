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

    def test_render_png_declares_render_meta_schema(self):
        """render_png must publish a typed schema for its metadata.

        Its dimensions, DPI and ray flag used to reach the caller only inside an
        English sentence. They are now structuredContent validated against
        RenderMeta, so the schema has to be present and has to carry those
        fields.

        Subprocess, because conftest stubs FastMCP in-process, so decorator and
        schema behaviour is invisible to every other test in the suite.
        """
        import subprocess
        result = subprocess.run(
            [
                sys.executable, "-c",
                "import json; from pymol_mcp.server import mcp; "
                "t = mcp._tool_manager.get_tool('render_png'); "
                "s = t.fn_metadata.output_schema; "
                "print('schema=' + json.dumps(s))"
            ],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"Failed to inspect render_png:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "schema=null" not in result.stdout, (
            "render_png publishes no output schema. Its metadata is then "
            "text-only and a caller has to parse prose to get the dimensions."
        )
        for field in ("path", "width", "height", "dpi", "ray"):
            assert field in result.stdout, f"{field} missing from output schema"

    def test_render_png_returns_image_block_and_structured_metadata(self):
        """Both MCP channels must be used: bytes as content, facts as structured.

        Regression guard for two failure modes at once. Putting the Image inside
        the structured payload raises PydanticSerializationError, which broke
        every call. Dropping the ImageContent block loses the picture. The shape
        that satisfies both is CallToolResult carrying content plus
        structuredContent.
        """
        import struct
        import subprocess
        import zlib

        def chunk(kind: bytes, data: bytes) -> bytes:
            checksum = zlib.crc32(kind)
            checksum = zlib.crc32(data, checksum) & 0xFFFFFFFF
            return (
                struct.pack(">I", len(data))
                + kind
                + data
                + struct.pack(">I", checksum)
            )

        png_path = REPO_ROOT / "tests" / "_tmp_integrity_render.png"
        png_path.write_bytes(
            b"\x89PNG\r\n\x1a\x08"[:8].replace(b"\x08", b"\n")
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 640, 480, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", b"")
            + chunk(b"IEND", b"")
        )
        try:
            result = subprocess.run(
                [
                    sys.executable, "-c",
                    "import sys, json; "
                    "from pathlib import Path; "
                    "from pymol_mcp.server import mcp, _image_result; "
                    "from pymol_mcp.api import RenderMeta; "
                    "p = sys.argv[1]; "
                    "meta = RenderMeta(path=p, width=640, height=480, "
                    "dpi=300, ray=True); "
                    "res = _image_result(meta, 'Rendered 640x480', "
                    "Path(p), 'image/png'); "
                    "t = mcp._tool_manager.get_tool('render_png'); "
                    "out = t.fn_metadata.convert_result(res); "
                    "print('kinds=' + ','.join(c.type for c in out.content)); "
                    "print('mime=' + out.content[1].mimeType); "
                    "print('structured=' + json.dumps(out.structuredContent))",
                    str(png_path),
                ],
                capture_output=True, text=True,
                cwd=str(REPO_ROOT),
            )
            assert result.returncode == 0, (
                f"render_png result conversion failed:\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
            assert "kinds=text,image" in result.stdout, result.stdout
            assert "mime=image/png" in result.stdout
            assert '"width": 640' in result.stdout
            assert '"ray": true' in result.stdout
        finally:
            png_path.unlink(missing_ok=True)

    def test_image_inside_structured_payload_still_fails(self):
        """Documents why the CallToolResult shape is necessary, not stylistic.

        An Image reached through an ordinary structured return cannot be
        serialised by pydantic, and the call fails at runtime rather than at
        import. Keeping this pinned means the reason for the indirection in
        _image_result stays discoverable, and we find out if the SDK ever gains
        support for it.
        """
        import subprocess
        result = subprocess.run(
            [
                sys.executable, "-c",
                "from typing import Any; "
                "from mcp.server.fastmcp.utilities.func_metadata "
                "import func_metadata; "
                "from mcp.server.fastmcp.utilities.types import Image; "
                "f = lambda: None; "
                "f.__annotations__ = {'return': dict[str, Any]}; "
                "md = func_metadata(f); "
                "print('schema_built=' + str(md.output_schema is not None)); "
                "r = None\n"
                "try:\n"
                "    md.convert_result({'img': Image(data=b'x', format='png')})\n"
                "    print('SERIALISED')\n"
                "except Exception as e:\n"
                "    print('RAISED ' + type(e).__name__)\n"
            ],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"probe failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "schema_built=True" in result.stdout, result.stdout
        assert "RAISED" in result.stdout, (
            "An Image inside a structured payload now serialises. If the SDK "
            "gained support for this, the CallToolResult indirection in "
            "_image_result may no longer be needed."
        )

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
