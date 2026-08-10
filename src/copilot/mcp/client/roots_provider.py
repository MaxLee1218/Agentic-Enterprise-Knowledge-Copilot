"""Tenant-bound roots provider with traversal and symlink escape protection."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from copilot.mcp.errors import MCPAuthorizationError, MCPConfigurationError


class MCPRootsProvider:
    def __init__(self, *, tenant_id: str, approved_roots: tuple[tuple[str, Path], ...]) -> None:
        if not tenant_id:
            raise MCPConfigurationError("Tenant is required for MCP roots")
        resolved: list[tuple[str, Path]] = []
        for root_tenant, path in approved_roots:
            candidate = path.resolve(strict=True)
            if candidate == Path("/") or candidate == Path.home().resolve():
                raise MCPConfigurationError("Broad host roots cannot be exposed through MCP")
            if candidate.name in {".ssh", ".aws", ".config"} or (candidate / ".env").exists():
                raise MCPConfigurationError("Credential-bearing roots cannot be exposed")
            resolved.append((root_tenant, candidate))
        self._tenant_id = tenant_id
        self._roots = tuple(resolved)

    def __call__(self) -> tuple[tuple[str, str | None], ...]:
        visible = tuple(path for tenant, path in self._roots if tenant == self._tenant_id)
        if not visible:
            raise MCPAuthorizationError("No MCP root is approved for this tenant")
        return tuple((f"file://{quote(str(path))}", path.name) for path in visible)


__all__ = ["MCPRootsProvider"]
