"""Pydantic model validation."""


import pytest

from pymol_mcp.models import (
    CommandDef,
    ErrorCategory,
    ParameterDef,
    ParseResult,
    SocketRequest,
    SocketResponse,
)
from pymol_mcp.server import (
    PyMOLConnection,
    parse_pymol_input,
)

# ============================================================================
# 17. PYDANTIC MODEL VALIDATION
# ============================================================================


class TestPydanticModels:
    """Test Pydantic model validation rules."""

    # --- ParameterDef ---

    def test_parameter_def_valid(self):
        p = ParameterDef(name="test", required=True)
        assert p.name == "test"
        assert p.required is True
        assert p.default is None
        assert p.options == []

    def test_parameter_def_with_options(self):
        p = ParameterDef(name="rep", required=True, options=["a", "b", "c"])
        assert p.options == ["a", "b", "c"]

    def test_parameter_def_duplicate_options_rejected(self):
        with pytest.raises(Exception, match="[Dd]uplicate"):
            ParameterDef(name="rep", required=True, options=["a", "b", "a"])

    # --- CommandDef ---

    def test_command_def_valid(self):
        cmd = CommandDef(
            description="Test command",
            pattern=r"^test$",
            parameters=[],
            check_selection=False,
        )
        assert cmd.description == "Test command"
        assert cmd.composite is False

    def test_command_def_invalid_regex_rejected(self):
        with pytest.raises(Exception, match="[Ii]nvalid regex"):
            CommandDef(
                description="Bad",
                pattern=r"^test(",
                parameters=[],
                check_selection=False,
            )

    def test_command_def_unanchored_start_rejected(self):
        with pytest.raises(Exception, match="anchored at start"):
            CommandDef(
                description="Bad",
                pattern=r"test$",
                parameters=[],
                check_selection=False,
            )

    def test_command_def_unanchored_end_rejected(self):
        with pytest.raises(Exception, match="anchored at end"):
            CommandDef(
                description="Bad",
                pattern=r"^test",
                parameters=[],
                check_selection=False,
            )

    def test_command_def_empty_description_rejected(self):
        with pytest.raises(Exception, match="[Dd]escription"):
            CommandDef(
                description="",
                pattern=r"^test$",
                parameters=[],
                check_selection=False,
            )

    def test_command_def_whitespace_description_rejected(self):
        with pytest.raises(Exception, match="[Dd]escription"):
            CommandDef(
                description="   ",
                pattern=r"^test$",
                parameters=[],
                check_selection=False,
            )

    def test_command_def_duplicate_param_names_rejected(self):
        with pytest.raises(Exception, match="[Dd]uplicate"):
            CommandDef(
                description="Bad",
                pattern=r"^test$",
                parameters=[
                    ParameterDef(name="a", required=True),
                    ParameterDef(name="a", required=False),
                ],
                check_selection=False,
            )

    # --- ErrorCategory ---

    def test_error_category_valid(self):
        ec = ErrorCategory(label="SYNTAX_ERROR", patterns=[r"Syntax error"])
        assert ec.label == "SYNTAX_ERROR"

    def test_error_category_lowercase_label_rejected(self):
        with pytest.raises(Exception, match="UPPERCASE"):
            ErrorCategory(label="syntax_error", patterns=[r"Syntax error"])

    def test_error_category_invalid_regex_rejected(self):
        with pytest.raises(Exception, match="[Ii]nvalid regex"):
            ErrorCategory(label="BAD", patterns=[r"("])

    # --- ParseResult ---

    def test_parse_result_tuple_unpacking(self):
        pr = ParseResult(command="show", args={"representation": "cartoon"})
        cmd, args = pr
        assert cmd == "show"
        assert args == {"representation": "cartoon"}

    def test_parse_result_attribute_access(self):
        pr = ParseResult(command="fetch", args={"code": "1ubq"})
        assert pr.command == "fetch"
        assert pr.args == {"code": "1ubq"}

    def test_parse_result_from_parse_pymol_input(self):
        """parse_pymol_input returns ParseResult that supports unpacking."""
        result = parse_pymol_input("show cartoon")
        assert isinstance(result, ParseResult)
        cmd, args = result
        assert cmd == "show"

    # --- SocketRequest ---

    def test_socket_request_serialization(self):
        req = SocketRequest(command="show", args={"representation": "sticks"})
        # exclude_none is what send_command uses, so an unset source keeps the
        # payload identical to what the plugin saw before the field existed.
        data = req.model_dump(exclude_none=True)
        assert data == {
            "type": "structured_command",
            "command": "show",
            "args": {"representation": "sticks"},
        }

    def test_socket_request_carries_source_when_set(self):
        req = SocketRequest(
            command="show",
            args={"representation": "sticks"},
            source="show sticks",
        )
        assert req.model_dump(exclude_none=True)["source"] == "show sticks"

    def test_socket_request_keeps_audit_and_replay_separate(self):
        req = SocketRequest(
            command="show",
            args={"representation": "sticks"},
            source="typed apply(show)",
            replay="show sticks, all",
        )
        data = req.model_dump(exclude_none=True)
        assert data["source"] == "typed apply(show)"
        assert data["replay"] == "show sticks, all"

    def test_socket_request_default_type(self):
        req = SocketRequest(command="fetch", args={"code": "1ubq"})
        assert req.type == "structured_command"

    # --- SocketResponse ---

    def test_socket_response_success(self):
        resp = SocketResponse(status="success", result={"output": "OK"})
        assert resp.status == "success"
        assert resp.result == {"output": "OK"}
        assert resp.message is None

    def test_socket_response_error(self):
        resp = SocketResponse(status="error", message="Not found")
        assert resp.status == "error"
        assert resp.message == "Not found"

    def test_socket_response_invalid_status_rejected(self):
        with pytest.raises(Exception):
            SocketResponse(status="unknown")

    # --- PyMOLConnection port validation ---

    def test_port_validation_valid(self):
        conn = PyMOLConnection(port=9876)
        assert conn.port == 9876

    def test_port_validation_min(self):
        conn = PyMOLConnection(port=1)
        assert conn.port == 1

    def test_port_validation_max(self):
        conn = PyMOLConnection(port=65535)
        assert conn.port == 65535

    def test_port_validation_zero_rejected(self):
        with pytest.raises(ValueError, match="[Pp]ort"):
            PyMOLConnection(port=0)

    def test_port_validation_negative_rejected(self):
        with pytest.raises(ValueError, match="[Pp]ort"):
            PyMOLConnection(port=-1)

    def test_port_validation_too_large_rejected(self):
        with pytest.raises(ValueError, match="[Pp]ort"):
            PyMOLConnection(port=65536)
