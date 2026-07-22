"""Error pattern detection in PyMOL output."""

import re

import pytest

from pymol_mcp.server import (
    ERROR_PATTERNS,
    analyze_pymol_output,
)

# ============================================================================
# 7. ERROR PATTERN DETECTION
# ============================================================================


class TestAnalyzePymolOutput:
    """Test analyze_pymol_output() error detection."""

    def test_no_error(self):
        assert analyze_pymol_output("Loaded structure successfully") is None

    def test_empty_string(self):
        assert analyze_pymol_output("") is None

    @pytest.mark.parametrize("output,expected_label", [
        ("Syntax error in command", "SYNTAX_ERROR"),
        ("invalid syntax near token", "SYNTAX_ERROR"),
        ("Unknown command: xyz", "SYNTAX_ERROR"),
    ])
    def test_syntax_errors(self, output, expected_label):
        result = analyze_pymol_output(output)
        assert result is not None
        assert expected_label in result

    @pytest.mark.parametrize("output,expected_label", [
        ("Invalid selection: foobar", "SELECTION_ERROR"),
        ("No atoms selected", "SELECTION_ERROR"),
        ("Selection not found", "SELECTION_ERROR"),
    ])
    def test_selection_errors(self, output, expected_label):
        result = analyze_pymol_output(output)
        assert result is not None
        assert expected_label in result

    @pytest.mark.parametrize("output,expected_label", [
        ("object myobj not found", "OBJECT_NOT_FOUND"),
        ("Object foo does not exist", "OBJECT_NOT_FOUND"),
    ])
    def test_object_errors(self, output, expected_label):
        result = analyze_pymol_output(output)
        assert result is not None
        assert expected_label in result

    @pytest.mark.parametrize("output,expected_label", [
        ("Unable to open file /tmp/foo.pdb", "FILE_ERROR"),
        ("No such file: bar.pdb", "FILE_ERROR"),
        ("Permission denied", "FILE_ERROR"),
    ])
    def test_file_errors(self, output, expected_label):
        result = analyze_pymol_output(output)
        assert result is not None
        assert expected_label in result

    @pytest.mark.parametrize("output,expected_label", [
        ("Connection refused", "CONNECTION_ERROR"),
        ("Timeout waiting for response", "CONNECTION_ERROR"),
        ("Failed to fetch 1abc", "CONNECTION_ERROR"),
    ])
    def test_connection_errors(self, output, expected_label):
        result = analyze_pymol_output(output)
        assert result is not None
        assert expected_label in result

    @pytest.mark.parametrize("output,expected_label", [
        ("Incorrect number of parameters", "PARAMETER_ERROR"),
        ("Invalid parameter value", "PARAMETER_ERROR"),
    ])
    def test_parameter_errors(self, output, expected_label):
        result = analyze_pymol_output(output)
        assert result is not None
        assert expected_label in result

    def test_case_insensitive_detection(self):
        """Error detection should be case-insensitive."""
        result = analyze_pymol_output("SYNTAX ERROR in line 1")
        assert result is not None
        assert "SYNTAX_ERROR" in result


# ============================================================================
# 12. ERROR_PATTERNS STRUCTURE
# ============================================================================


class TestErrorPatternsStructure:
    """Validate ERROR_PATTERNS definitions."""

    def test_all_error_patterns_are_valid_regex(self):
        for label, patterns in ERROR_PATTERNS.items():
            assert isinstance(patterns, list), f"{label} is not a list"
            for p in patterns:
                try:
                    re.compile(p, re.IGNORECASE)
                except re.error as e:
                    pytest.fail(f"Error pattern '{p}' in {label} is invalid: {e}")

    def test_all_labels_are_uppercase(self):
        for label in ERROR_PATTERNS:
            assert label == label.upper(), (
                f"Error label '{label}' should be UPPERCASE"
            )


# ============================================================================
# 13. COMPLEX REAL-WORLD COMMAND SEQUENCES
# ============================================================================
