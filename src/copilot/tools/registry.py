"""Instance-scoped registry for governed tool plugins."""

from __future__ import annotations

import re
from collections.abc import Collection
from threading import RLock

from copilot.contracts import CapabilityName, RiskLevel, ToolDefinition
from copilot.tools.base import Tool
from copilot.tools.exceptions import (
    ToolAlreadyExistsError,
    ToolDefinitionValidationError,
    ToolNotFoundError,
)
from copilot.tools.schema import validate_schema_definition

_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def validate_tool_name(name: str) -> str:
    """Validate the canonical v1 tool-name syntax without silently normalizing it."""
    if not _TOOL_NAME_PATTERN.fullmatch(name):
        raise ToolDefinitionValidationError(
            "Tool name must be 1-64 lowercase letters, digits, or underscores "
            "and start with a letter"
        )
    return name


class ToolRegistry:
    """Thread-safe, non-global mapping from approved names to tool plugins."""

    def __init__(
        self,
        allowed_names: Collection[str] | None = None,
        allowed_risk_levels: Collection[RiskLevel] | None = None,
    ) -> None:
        approved = allowed_names if allowed_names is not None else tuple(CapabilityName)
        approved_risks = (
            allowed_risk_levels
            if allowed_risk_levels is not None
            else (RiskLevel.LOW, RiskLevel.MEDIUM)
        )
        self._allowed_names = frozenset(str(name) for name in approved)
        self._allowed_risk_levels = frozenset(approved_risks)
        self._tools: dict[str, Tool] = {}
        self._lock = RLock()

    def register(self, tool: Tool) -> None:
        """Validate and bind a plugin exactly once by its stable name."""
        definition = tool.definition
        name = validate_tool_name(definition.tool_name)
        if name not in self._allowed_names:
            raise ToolDefinitionValidationError(
                f"Tool '{name}' is not approved by this registry configuration"
            )
        if definition.risk_level not in self._allowed_risk_levels:
            raise ToolDefinitionValidationError(
                f"Risk level '{definition.risk_level}' is not approved by this registry"
            )
        validate_schema_definition(definition.input_schema.root, "input")
        validate_schema_definition(definition.output_schema.root, "output")
        with self._lock:
            if name in self._tools:
                raise ToolAlreadyExistsError(name)
            self._tools[name] = tool

    def unregister(self, name: str) -> None:
        """Remove a registered plugin or reject an unknown name."""
        validate_tool_name(name)
        with self._lock:
            if name not in self._tools:
                raise ToolNotFoundError(name)
            del self._tools[name]

    def get(self, name: str) -> Tool:
        """Return the plugin registered under a stable name."""
        validate_tool_name(name)
        with self._lock:
            try:
                return self._tools[name]
            except KeyError as exc:
                raise ToolNotFoundError(name) from exc

    def list(self) -> list[ToolDefinition]:
        """Return immutable definitions in deterministic name order."""
        with self._lock:
            return [self._tools[name].definition for name in sorted(self._tools)]

    def contains(self, name: str) -> bool:
        """Report whether a syntactically valid name is registered."""
        validate_tool_name(name)
        with self._lock:
            return name in self._tools
