"""Shared test setup.

pytest imports conftest before any test module, which is what lets the MCP
stub be installed before anything imports the server.
"""

import importlib.util
import socket
import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_PATH = REPO_ROOT / "pymol-mcp-socket-plugin" / "__init__.py"
SCRIPTS_DIR = REPO_ROOT / "scripts"

# FastMCP's initialisation fails outside a real MCP runtime, so stub the
# framework. TestServerModuleIntegrity checks the real import in a subprocess,
# where this stub is not in effect.
#
# `mcp.types` is deliberately kept real. It is pure pydantic data -- content
# blocks, request/result shapes -- with no runtime to fail, and stubbing it would
# turn every CallToolResult and TextContent in a test into a MagicMock that
# asserts nothing. It has to be imported before the stub goes in, because
# replacing `mcp` makes it a non-package and `import mcp.types` then fails.
import mcp.types as _real_mcp_types  # noqa: E402

for _name in ("mcp", "mcp.server", "mcp.server.fastmcp"):
    sys.modules[_name] = MagicMock()

sys.modules["mcp.types"] = _real_mcp_types
sys.modules["mcp"].types = _real_mcp_types


def free_ports(count=1):
    """A *contiguous* block of unused ports.

    Tests must not use the real 9876-9895 range: a developer with PyMOL open
    would have their live instance discovered by the test suite.

    Contiguity is the load-bearing part. Callers hand this to PORT_RANGE as
    `range(min(ports), max(ports) + 1)`, and three ports picked independently
    from the ephemeral range are nowhere near each other -- 44800 and 52460
    made a 7,661-port scan. Discovery then swept up listeners belonging to
    other tests, so a test that started no instance found two and failed, and
    one asserting an exact port list found extras. It presented as flakiness
    because which ports the OS handed out varied per run.

    Asking for one port and probing the rest of the block also double-checks
    that nothing already holds them, which the previous bind-then-close could
    not do for ports it never touched.
    """
    if count < 1:
        raise ValueError("count must be at least 1")

    for _ in range(100):
        probe = socket.socket()
        probe.bind(("localhost", 0))
        first = probe.getsockname()[1]
        probe.close()

        if first + count - 1 > 65535:
            continue

        socks = []
        try:
            for offset in range(count):
                s = socket.socket()
                s.bind(("localhost", first + offset))
                socks.append(s)
        except OSError:
            continue  # something holds a port in the block; pick another
        finally:
            for s in socks:
                s.close()

        if len(socks) == count:
            return list(range(first, first + count))

    raise RuntimeError(f"could not find {count} contiguous free ports")


def free_port():
    """A single unused port."""
    return free_ports(1)[0]


def load_plugin(name="pymol_plugin"):
    """Import the PyMOL plugin from its path, the way PyMOL itself does.

    Each call returns an independent module object. Tests that depend on
    import-time state, such as the PYMOL_MCP_HISTORY setting, need their own.
    """
    spec = importlib.util.spec_from_file_location(name, PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_script(stem):
    """Import one of the install scripts from scripts/ without running main()."""
    spec = importlib.util.spec_from_file_location(
        f"script_{stem}", SCRIPTS_DIR / f"{stem}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
