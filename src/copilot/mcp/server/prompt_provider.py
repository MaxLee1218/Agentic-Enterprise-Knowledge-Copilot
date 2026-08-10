"""Versioned explicit prompt export provider; internal system prompts stay private."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from copilot.contracts import (
    JsonObject,
    MCPClientIdentity,
    MCPOrigin,
    MCPPromptCapability,
    MCPProtocolRevision,
    MCPProvenance,
    MCPTransport,
)
from copilot.contracts.validators import utc_now
from copilot.mcp.errors import MCPCapabilityNotFoundError
from copilot.mcp.protocol import MCPPromptResult
from copilot.mcp.server.authorization import MCPServerAuthorization


@dataclass(frozen=True, slots=True)
class MCPExportedPrompt:
    name: str
    description: str
    version: str
    arguments_schema: JsonObject
    messages: tuple[JsonObject, ...]
    allowed_tenants: tuple[str, ...]
    discover_scopes: tuple[str, ...] = ("mcp.prompts.read",)


class MCPPromptProvider:
    def __init__(
        self,
        *,
        prompts: tuple[MCPExportedPrompt, ...],
        authorization: MCPServerAuthorization,
        server_id: str,
        namespace: str,
    ) -> None:
        self._prompts = {item.name: item for item in prompts}
        self._authorization = authorization
        self._server_id = server_id
        self._namespace = namespace

    def list(self, identity: MCPClientIdentity) -> tuple[MCPPromptCapability, ...]:
        result: list[MCPPromptCapability] = []
        for item in self._prompts.values():
            try:
                self._authorization.authorize(
                    identity,
                    required_scopes=item.discover_scopes,
                    allowed_tenants=item.allowed_tenants,
                )
            except Exception:
                continue
            digest = hashlib.sha256(item.arguments_schema.model_dump_json().encode()).hexdigest()
            result.append(
                MCPPromptCapability(
                    capability_id=f"{self._server_id}:prompt:{item.name}",
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
                    arguments_schema=item.arguments_schema,
                    version=item.version,
                )
            )
        return tuple(result)

    def get(self, name: str, arguments: JsonObject, identity: MCPClientIdentity) -> MCPPromptResult:
        item = self._prompts.get(name)
        if item is None:
            raise MCPCapabilityNotFoundError("MCP prompt is not exported")
        self._authorization.authorize(
            identity,
            required_scopes=item.discover_scopes,
            allowed_tenants=item.allowed_tenants,
        )
        # Arguments are validated by the protocol schema and remain user data. Providers may
        # define dedicated templates later; this baseline never rewrites trusted prompt roles.
        del arguments
        return MCPPromptResult(item.description, item.messages)


__all__ = ["MCPExportedPrompt", "MCPPromptProvider"]
