"""Approved stdio transport composition."""

from __future__ import annotations

from copilot.contracts import MCPConnection, MCPTransport
from copilot.mcp.errors import MCPConfigurationError
from copilot.mcp.protocol import MCPProtocolClient
from copilot.mcp.security.connection_policy import MCPConnectionPolicy


class StdioTransportFactory:
    def __init__(self, policy: MCPConnectionPolicy) -> None:
        self._policy = policy

    def create(self, connection: MCPConnection, *, tenant_id: str) -> MCPProtocolClient:
        if connection.transport is not MCPTransport.STDIO:
            raise MCPConfigurationError("Connection is not configured for stdio")
        self._policy.validate(connection, tenant_id=tenant_id)
        return MCPProtocolClient(connection)


__all__ = ["StdioTransportFactory"]
