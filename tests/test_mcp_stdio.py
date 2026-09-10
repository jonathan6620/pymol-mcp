"""Exercise the public MCP transport in a process without conftest's mocks."""

import subprocess
import sys
import textwrap

from conftest import REPO_ROOT


def test_real_stdio_initialize_discover_and_call():
    # Disable PyMOL discovery in the child: this protocol test must never inspect
    # a developer's live session and needs neither PyMOL nor network access.
    script = textwrap.dedent('''
        import asyncio
        import sys
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def check():
            params = StdioServerParameters(
                command=sys.executable,
                args=["-c", "from pymol_mcp import server; "
                      "server.PORT_RANGE = range(0); server.main()"],
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert initialized.capabilities.tools is not None
                    listing = await session.list_tools()
                    names = {tool.name for tool in listing.tools}
                    expected = {"list_commands", "render_png", "count", "label_text"}
                    expected.update({"translate", "set_setting"})
                    assert expected <= names
                    result = await session.call_tool(
                        "list_commands", {"filter": "fetch"}
                    )
                    assert not result.isError, result
                    assert any("fetch" in item.text for item in result.content
                               if item.type == "text"), result
                    bad = await session.call_tool("list_commands", {"filter": []})
                    assert bad.isError, bad
            print("MCP round trip passed")

        asyncio.run(asyncio.wait_for(check(), timeout=20))
    ''')
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MCP round trip passed" in result.stdout
