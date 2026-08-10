"""Protocol-neutral MCP transport facade contracts."""

from __future__ import annotations

from typing import Protocol

from copilot.contracts import MCPConnection
from copilot.mcp.protocol import MCPProtocolClient


class MCPTransportFactory(Protocol):
    """Create an isolated protocol client after transport-specific policy validation."""

    def create(self, connection: MCPConnection, *, tenant_id: str) -> MCPProtocolClient: ...


__all__ = ["MCPTransportFactory"]
