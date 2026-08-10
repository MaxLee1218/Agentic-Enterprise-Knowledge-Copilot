"""Explicit safe MCP resources; repositories/filesystems are never exposed implicitly."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from copilot.contracts import (
    MCPClientIdentity,
    MCPOrigin,
    MCPProtocolRevision,
    MCPProvenance,
    MCPResourceCapability,
    MCPTransport,
)
from copilot.contracts.validators import utc_now
from copilot.mcp.errors import MCPCapabilityNotFoundError
from copilot.mcp.protocol import MCPResourceContent
from copilot.mcp.server.authorization import MCPServerAuthorization


@dataclass(frozen=True, slots=True)
class MCPExportedResource:
    name: str
    uri: str
    description: str
    content: str
    mime_type: str
    allowed_tenants: tuple[str, ...]
    discover_scopes: tuple[str, ...] = ("mcp.resources.read",)


class MCPResourceProvider:
    def __init__(
        self,
        *,
        resources: tuple[MCPExportedResource, ...],
        authorization: MCPServerAuthorization,
        server_id: str,
        namespace: str,
    ) -> None:
        self._resources = {item.uri: item for item in resources}
        self._authorization = authorization
        self._server_id = server_id
        self._namespace = namespace

    def list(self, identity: MCPClientIdentity) -> tuple[MCPResourceCapability, ...]:
        result: list[MCPResourceCapability] = []
        for item in self._resources.values():
            try:
                self._authorization.authorize(
                    identity,
                    required_scopes=item.discover_scopes,
                    allowed_tenants=item.allowed_tenants,
                )
            except Exception:
                continue
            digest = hashlib.sha256(item.content.encode("utf-8")).hexdigest()
            result.append(
                MCPResourceCapability(
                    capability_id=f"{self._server_id}:resource:{item.name}",
                    name=item.name,
                    description=item.description,
                    namespace=self._namespace,
                    origin=MCPOrigin(
                        server_id=self._server_id,
                        connection_id=f"{self._server_id}-export",
                        namespace=self._namespace,
                        transport=MCPTransport.STREAMABLE_HTTP,
                        endpoint_fingerprint=hashlib.sha256(self._server_id.encode()).hexdigest(),
                    ),
                    provenance=MCPProvenance(
                        protocol_revision=MCPProtocolRevision.V2025_11_25,
                        server_version="0.1.0",
                        schema_digest=digest,
                        discovered_at=utc_now(),
                    ),
                    required_scopes=item.discover_scopes,
                    uri=item.uri,
                    mime_type=item.mime_type,
                )
            )
        return tuple(result)

    def read(self, uri: str, identity: MCPClientIdentity) -> MCPResourceContent:
        item = self._resources.get(uri)
        if item is None:
            raise MCPCapabilityNotFoundError("MCP resource is not exported")
        self._authorization.authorize(
            identity,
            required_scopes=item.discover_scopes,
            allowed_tenants=item.allowed_tenants,
        )
        return MCPResourceContent(uri=item.uri, mime_type=item.mime_type, text=item.content)


__all__ = ["MCPExportedResource", "MCPResourceProvider"]
