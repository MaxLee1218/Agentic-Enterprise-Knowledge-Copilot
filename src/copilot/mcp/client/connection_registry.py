"""Instance-scoped registry of approved non-secret MCP connection configurations."""

from __future__ import annotations

from threading import RLock

from copilot.contracts import MCPConnection
from copilot.mcp.errors import MCPConfigurationError


class MCPConnectionRegistry:
    def __init__(self) -> None:
        self._connections: dict[str, MCPConnection] = {}
        self._namespaces: dict[str, str] = {}
        self._lock = RLock()

    def register(self, connection: MCPConnection) -> None:
        with self._lock:
            if connection.connection_id in self._connections:
                raise MCPConfigurationError("MCP connection already exists")
            owner = self._namespaces.get(connection.namespace)
            if owner is not None and owner != connection.connection_id:
                raise MCPConfigurationError("MCP namespace is already owned")
            self._connections[connection.connection_id] = connection
            self._namespaces[connection.namespace] = connection.connection_id

    def get(self, connection_id: str) -> MCPConnection:
        with self._lock:
            try:
                return self._connections[connection_id]
            except KeyError as exc:
                raise MCPConfigurationError("MCP connection is not registered") from exc

    def list(self) -> tuple[MCPConnection, ...]:
        with self._lock:
            return tuple(self._connections[key] for key in sorted(self._connections))

    def unregister(self, connection_id: str) -> MCPConnection:
        with self._lock:
            try:
                connection = self._connections.pop(connection_id)
            except KeyError as exc:
                raise MCPConfigurationError("MCP connection is not registered") from exc
            self._namespaces.pop(connection.namespace, None)
            return connection


__all__ = ["MCPConnectionRegistry"]
