"""Canonical Streamable HTTP transport composition."""

from __future__ import annotations

from copilot.contracts import MCPConnection, MCPTransport
from copilot.mcp.errors import MCPConfigurationError
from copilot.mcp.protocol import MCPProtocolClient
from copilot.mcp.security.connection_policy import MCPConnectionPolicy


class StreamableHTTPTransportFactory:
    def __init__(self, policy: MCPConnectionPolicy) -> None:
        self._policy = policy

    def create(self, connection: MCPConnection, *, tenant_id: str) -> MCPProtocolClient:
        if connection.transport is not MCPTransport.STREAMABLE_HTTP:
            raise MCPConfigurationError("Connection is not configured for Streamable HTTP")
        validated = self._policy.validate(connection, tenant_id=tenant_id)
        if validated is None:  # pragma: no cover
            raise MCPConfigurationError("HTTP endpoint validation did not run")
        canonical = connection.model_copy(update={"endpoint": validated.canonical_endpoint})
        return MCPProtocolClient(canonical)


__all__ = ["StreamableHTTPTransportFactory"]
