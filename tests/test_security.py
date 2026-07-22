"""Rails against code execution reaching PyMOL.

The allowlist stops a caller naming an arbitrary cmd.* function, but three
dispatcher entries still routed caller strings into something that evaluates
them. All were confirmed to execute code before being fixed:

  alter / alter_state   PyMOL evaluates the expression as Python, per atom
  label                 same evaluation, different command
  util.* and spheroid   arguments were concatenated into cmd.do(), PyMOL's
                        command interpreter, so a newline in the selection
                        appended a second command such as `run evil.pml`

The guards live in the plugin rather than the MCP server on purpose: the socket
is the trust boundary, and any local process can connect to it directly without
going through the server or its regexes.
"""

import ast

import pytest
from conftest import PLUGIN_PATH, load_plugin


@pytest.fixture(scope="module")
def plugin():
    return load_plugin("plugin_security")


@pytest.fixture(scope="module")
def plugin_source():
    return PLUGIN_PATH.read_text()


ATTACKS = [
    pytest.param("__import__('os').system('id')", id="import-os"),
    pytest.param(
        "__import__('pathlib').Path('/tmp/pwned').write_text('x')", id="file-write"
    ),
    pytest.param("().__class__.__bases__[0].__subclasses__()", id="subclass-escape"),
    pytest.param("open('/etc/passwd').read()", id="open-file"),
    pytest.param("eval('1+1')", id="eval"),
    pytest.param("exec('x=1')", id="exec"),
    pytest.param("[c for c in ().__class__.__mro__]", id="comprehension"),
    pytest.param("(lambda: __import__('os'))()", id="lambda"),
    pytest.param("b.__class__", id="attribute-access"),
    pytest.param("globals()", id="globals"),
    pytest.param("getattr(b, 'real')", id="getattr"),
    pytest.param("b if b else __import__('os')", id="hidden-in-conditional"),
]

LEGITIMATE = [
    "b + 10",
    "b * 2",
    "'A'",
    "chain",
    "resi",
    "'CA' if name == 'CA' else name",
    "int(resi) + 100",
    "b / 2 + q",
    "resn + resi",
    "float(b) * 1.5",
    "max(b, 0)",
    "'X' if b > 50 else 'Y'",
]


class TestAlterExpressionIsRestricted:
    @pytest.mark.parametrize("expression", ATTACKS)
    def test_code_execution_is_rejected(self, plugin, expression):
        with pytest.raises(ValueError):
            plugin.check_atom_expression(expression)

    @pytest.mark.parametrize("expression", LEGITIMATE)
    def test_atom_property_formulas_still_work(self, plugin, expression):
        plugin.check_atom_expression(expression)

    def test_empty_expression_is_rejected(self, plugin):
        with pytest.raises(ValueError, match="non-empty"):
            plugin.check_atom_expression("   ")

    def test_syntax_error_is_reported_not_raised_raw(self, plugin):
        with pytest.raises(ValueError, match="not a valid expression"):
            plugin.check_atom_expression("b +")

    def test_unknown_names_are_rejected(self, plugin):
        """Fails closed: a name that is not a known atom property is refused
        rather than passed through to be resolved at eval time."""
        with pytest.raises(ValueError, match="not a known atom property"):
            plugin.check_atom_expression("mystery_name + 1")

    def test_coordinates_only_exist_for_alter_state(self, plugin):
        plugin.check_atom_expression("x * 2", coordinates=True)
        with pytest.raises(ValueError, match="not a known atom property"):
            plugin.check_atom_expression("x * 2")


class TestDispatcherEnforcesTheGuard:
    """The check must be wired into the handlers, not merely available."""

    @pytest.fixture
    def dispatcher(self, plugin):
        class FakeCmd:
            def __init__(self):
                self.calls = []

            def alter(self, selection, expression):
                self.calls.append(("alter", selection, expression))
                return "altered"

            def alter_state(self, state, selection, expression):
                self.calls.append(("alter_state", state, selection, expression))
                return "altered_state"

            def label(self, selection, expression):
                self.calls.append(("label", selection, expression))
                return "labelled"

            def __getattr__(self, name):
                return lambda *a, **k: None

        fake = FakeCmd()
        return fake, plugin.build_command_dispatcher(fake)

    def test_alter_handler_blocks_and_does_not_call_pymol(self, dispatcher):
        fake, dispatch = dispatcher
        with pytest.raises(ValueError):
            dispatch["alter"]({
                "selection": "all",
                "expression": "__import__('os').system('id')",
            })
        assert fake.calls == [], "PyMOL must not see a rejected expression"

    def test_alter_state_handler_blocks_too(self, dispatcher):
        fake, dispatch = dispatcher
        with pytest.raises(ValueError):
            dispatch["alter_state"]({
                "state": "1",
                "selection": "all",
                "expression": "open('/etc/passwd').read()",
            })
        assert fake.calls == []

    def test_legitimate_alter_reaches_pymol(self, dispatcher):
        fake, dispatch = dispatcher
        dispatch["alter"]({"selection": "chain A", "expression": "b + 1"})
        assert fake.calls == [("alter", "chain A", "b + 1")]

    def test_label_expression_is_guarded_too(self, dispatcher):
        """label evaluates its expression exactly like alter does."""
        fake, dispatch = dispatcher
        with pytest.raises(ValueError):
            dispatch["label"]({
                "selection": "all",
                "expression": "__import__('os').system('id')",
            })
        assert fake.calls == []

    def test_legitimate_label_reaches_pymol(self, dispatcher):
        fake, dispatch = dispatcher
        dispatch["label"]({"selection": "name CA", "expression": "resn+resi"})
        assert fake.calls == [("label", "name CA", "resn+resi")]


class TestNoCommandInterpreterInThePath:
    """util.* and spheroid used to build strings for cmd.do().

    cmd.do() runs PyMOL's command language, where a newline starts a new
    command, so a selection could smuggle in `run /path/evil.pml`.
    """

    def test_plugin_never_calls_cmd_do(self, plugin_source):
        """Parsed, not grepped, so prose mentioning cmd.do does not trip it."""
        calls = [
            node
            for node in ast.walk(ast.parse(plugin_source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "do"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "cmd"
        ]
        assert calls == [], (
            "cmd.do() puts PyMOL's command interpreter back in the dispatch "
            f"path (line {[c.lineno for c in calls]}); call the cmd.* or "
            "util.* function directly instead"
        )

    @pytest.mark.parametrize(
        "value",
        [
            "all\nrun /tmp/evil.pml",
            "all\r\nrun /tmp/evil.pml",
            "all\x00run",
        ],
        ids=["newline", "crlf", "null-byte"],
    )
    def test_control_characters_in_a_selection_are_rejected(self, plugin, value):
        with pytest.raises(ValueError, match="newlines or control characters"):
            plugin._reject_control_characters(value)

    def test_ordinary_selections_pass(self, plugin):
        for value in ["all", "chain A and resi 1-50", "polymer.protein", "resi 1+2+3"]:
            plugin._reject_control_characters(value)
