"""Deterministic MCP scope to existing internal permission mapping."""

from __future__ import annotations

from collections.abc import Mapping

from copilot.contracts import MCPScope
from copilot.mcp.errors import MCPScopeDeniedError

DEFAULT_SCOPE_MAP: dict[str, str] = {
    "mcp.tools.read": "task:read",
    "mcp.tools.invoke": "task:execute",
    "mcp.resources.read": "evidence:read",
    "mcp.prompts.read": "task:read",
    "mcp.sampling.invoke": "llm:sample",
    "mcp.elicitation.invoke": "interaction:elicit",
    "mcp.roots.read": "resource:roots",
}


class MCPScopeMapper:
    """Map known scopes and deny unknown or incomplete grants."""

    def __init__(self, mapping: Mapping[str, str] = DEFAULT_SCOPE_MAP) -> None:
        self._mapping = dict(mapping)

    def map(self, scopes: tuple[str, ...]) -> tuple[MCPScope, ...]:
        unknown = tuple(scope for scope in scopes if scope not in self._mapping)
        if unknown:
            raise MCPScopeDeniedError("MCP scope is not recognized")
        return tuple(
            MCPScope(external_scope=scope, internal_permission=self._mapping[scope])
            for scope in dict.fromkeys(scopes)
        )

    def require(self, granted: tuple[str, ...], required: tuple[str, ...]) -> None:
        if not set(required).issubset(granted):
            raise MCPScopeDeniedError("Required MCP scope is not granted")
        self.map(tuple(dict.fromkeys((*granted, *required))))


__all__ = ["DEFAULT_SCOPE_MAP", "MCPScopeMapper"]
