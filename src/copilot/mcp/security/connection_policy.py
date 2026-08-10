"""Deny-by-default validation for configured MCP server connections."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from copilot.contracts import MCPConnection, MCPTransport
from copilot.mcp.errors import MCPConfigurationError, MCPTenantViolationError
from copilot.mcp.security.origin_validator import MCPOriginValidator, ValidatedOrigin

_SENSITIVE_ENV_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY")


class MCPConnectionPolicy:
    """Authorize only canonical servers, namespaces, commands, roots, and tenants."""

    def __init__(
        self,
        *,
        approved_server_ids: Collection[str],
        approved_namespaces: Collection[str],
        approved_executables: Collection[Path] = (),
        approved_working_directories: Collection[Path] = (),
        origin_validator: MCPOriginValidator | None = None,
    ) -> None:
        self._servers = frozenset(approved_server_ids)
        self._namespaces = frozenset(approved_namespaces)
        self._executables = frozenset(path.resolve() for path in approved_executables)
        self._working_directories = tuple(path.resolve() for path in approved_working_directories)
        self._origin_validator = origin_validator

    def validate(self, connection: MCPConnection, *, tenant_id: str) -> ValidatedOrigin | None:
        if not connection.enabled:
            raise MCPConfigurationError("MCP connection is disabled")
        if connection.server.server_id not in self._servers:
            raise MCPConfigurationError("MCP server identity is not approved")
        if connection.namespace not in self._namespaces:
            raise MCPConfigurationError("MCP namespace is not approved")
        if connection.allowed_tenants and tenant_id not in connection.allowed_tenants:
            raise MCPTenantViolationError("Tenant is not approved for this MCP connection")
        if connection.transport is MCPTransport.STDIO:
            self._validate_stdio(connection)
            return None
        if self._origin_validator is None or connection.endpoint is None:
            raise MCPConfigurationError("Streamable HTTP origin policy is unavailable")
        return self._origin_validator.validate(connection.endpoint)

    def _validate_stdio(self, connection: MCPConnection) -> None:
        config = connection.stdio
        if config is None:
            raise MCPConfigurationError("stdio configuration is missing")
        executable = Path(config.executable)
        if not executable.is_absolute() or executable.resolve() not in self._executables:
            raise MCPConfigurationError("stdio executable is not on the fixed allowlist")
        if not executable.is_file():
            raise MCPConfigurationError("stdio executable is unavailable")
        working_directory = Path(config.working_directory)
        resolved_working_directory = working_directory.resolve()
        if not working_directory.is_absolute() or not any(
            resolved_working_directory == root or resolved_working_directory.is_relative_to(root)
            for root in self._working_directories
        ):
            raise MCPConfigurationError("stdio working directory is not approved")
        for key, value in config.environment.root.items():
            if not isinstance(value, str):
                raise MCPConfigurationError("stdio environment values must be strings")
            upper = key.upper()
            if any(marker in upper for marker in _SENSITIVE_ENV_MARKERS):
                raise MCPConfigurationError(
                    "stdio secrets must be resolved by the credential provider"
                )
            if len(key) > 100 or len(value) > 4096:
                raise MCPConfigurationError("stdio environment entry exceeds limits")


__all__ = ["MCPConnectionPolicy"]
