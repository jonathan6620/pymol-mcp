"""Pydantic models for the PyMOL MCP server."""

import re
from typing import Any, Dict, Iterator, List, Literal, Optional

from pydantic import BaseModel, field_validator, model_validator


class ParameterDef(BaseModel):
    """Single command parameter definition."""

    name: str
    required: bool
    default: Optional[str] = None
    options: List[str] = []

    @field_validator("options")
    @classmethod
    def no_duplicate_options(cls, v: List[str]) -> List[str]:
        if len(v) != len(set(v)):
            dupes = {x for x in v if v.count(x) > 1}
            raise ValueError(f"Duplicate options: {dupes}")
        return v


class CommandDef(BaseModel):
    """Full command definition."""

    description: str
    pattern: str
    parameters: List[ParameterDef]
    check_selection: bool
    composite: bool = False

    @field_validator("description")
    @classmethod
    def description_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Description must not be empty")
        return v

    @field_validator("pattern")
    @classmethod
    def pattern_valid_and_anchored(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}")
        if not v.startswith("^"):
            raise ValueError("Pattern must be anchored at start with ^")
        if not v.endswith("$"):
            raise ValueError("Pattern must be anchored at end with $")
        return v

    @model_validator(mode="after")
    def no_duplicate_param_names(self) -> "CommandDef":
        names = [p.name for p in self.parameters]
        if len(names) != len(set(names)):
            dupes = {x for x in names if names.count(x) > 1}
            raise ValueError(f"Duplicate parameter names: {dupes}")
        return self


class ErrorCategory(BaseModel):
    """Error pattern group (label + regex patterns)."""

    label: str
    patterns: List[str]

    @field_validator("label")
    @classmethod
    def label_uppercase(cls, v: str) -> str:
        if v != v.upper():
            raise ValueError(f"Error label must be UPPERCASE, got '{v}'")
        return v

    @field_validator("patterns")
    @classmethod
    def patterns_valid_regex(cls, v: List[str]) -> List[str]:
        for p in v:
            try:
                re.compile(p)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern '{p}': {e}")
        return v


class SocketRequest(BaseModel):
    """Outbound structured command message to PyMOL."""

    type: Literal["structured_command"] = "structured_command"
    command: str
    args: Dict[str, Any]
    # The literal PyMOL syntax this command was parsed from. The plugin writes
    # it to the replayable session script, and uses its absence to tell an
    # internal call (the connection health-check ping) from a real one.
    source: Optional[str] = None


class SocketResponse(BaseModel):
    """Inbound response message from PyMOL."""

    status: Literal["success", "error"]
    result: Optional[Any] = None
    message: Optional[str] = None


class ParseResult(BaseModel):
    """Return type of parse_pymol_input. Supports tuple unpacking."""

    command: str
    args: Dict[str, Any]

    def __iter__(self) -> Iterator:
        """Support tuple unpacking: cmd, args = parse_pymol_input(...)"""
        return iter((self.command, self.args))
