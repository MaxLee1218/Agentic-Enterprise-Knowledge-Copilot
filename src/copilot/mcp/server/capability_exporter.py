"""Explicit deny-by-default export allowlist for existing registered tools."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection
from dataclasses import dataclass

from copilot.contracts import (
    MCPClientIdentity,
    MCPOrigin,
    MCPProtocolRevision,
    MCPProvenance,
    MCPToolCapability,
    MCPTransport,
    RiskLevel,
)
from copilot.contracts.validators import utc_now
from copilot.mcp.errors import MCPCapabilityNotFoundError, MCPError
from copilot.mcp.server.authorization import MCPServerAuthorization
from copilot.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class MCPExportRule:
    tool_name: str
    allowed_tenants: tuple[str, ...]
    discover_scopes: tuple[str, ...] = ("mcp.tools.read",)
    invoke_scopes: tuple[str, ...] = ("mcp.tools.invoke",)
    require_approval: bool = False


class MCPCapabilityExporter:
    """Export nothing unless a local tool has a reviewed rule."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        rules: Collection[MCPExportRule],
        authorization: MCPServerAuthorization,
        server_id: str,
        namespace: str,
        server_version: str = "0.1.0",
        transport: MCPTransport = MCPTransport.STREAMABLE_HTTP,
    ) -> None:
        self._registry = registry
        self._rules = {rule.tool_name: rule for rule in rules}
        self._authorization = authorization
        self._server_id = server_id
        self._namespace = namespace
        self._server_version = server_version
        self._transport = transport

    def list(self, identity: MCPClientIdentity) -> tuple[MCPToolCapability, ...]:
        capabilities: list[MCPToolCapability] = []
        for name in sorted(self._rules):
            rule = self._rules[name]
            try:
                self._authorization.authorize(
                    identity,
                    required_scopes=rule.discover_scopes,
                    allowed_tenants=rule.allowed_tenants,
                )
            except MCPError:
                continue
            try:
                registration = self._registry.registration(name)
            except Exception:
                continue
            definition = registration.tool.definition
            schema_bytes = json.dumps(
                {
                    "input": definition.input_schema.root,
                    "output": definition.output_schema.root,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            capabilities.append(
                MCPToolCapability(
                    capability_id=f"{self._server_id}:tool:{name}",
                    name=name,
                    title=name.replace("_", " ").title(),
                    description=definition.description[:2048],
                    namespace=self._namespace,
                    origin=MCPOrigin(
                        server_id=self._server_id,
                        connection_id=f"{self._server_id}-export",
                        namespace=self._namespace,
                        transport=self._transport,
                        endpoint_fingerprint=hashlib.sha256(
                            f"{self._server_id}:{self._transport.value}".encode()
                        ).hexdigest(),
                    ),
                    provenance=MCPProvenance(
                        protocol_revision=MCPProtocolRevision.V2025_11_25,
                        server_version=self._server_version,
                        schema_digest=hashlib.sha256(schema_bytes).hexdigest(),
                        discovered_at=utc_now(),
                    ),
                    required_scopes=rule.invoke_scopes,
                    input_schema=definition.input_schema,
                    output_schema=definition.output_schema,
                    idempotent=definition.idempotency.idempotent,
                    read_only=definition.risk_level is RiskLevel.LOW,
                    destructive=definition.risk_level is RiskLevel.HIGH,
                )
            )
        return tuple(capabilities)

    def require_invocation(
        self, name: str, identity: MCPClientIdentity
    ) -> tuple[MCPToolCapability, MCPExportRule]:
        rule = self._rules.get(name)
        if rule is None:
            raise MCPCapabilityNotFoundError("MCP tool is not exported")
        self._authorization.authorize(
            identity,
            required_scopes=rule.invoke_scopes,
            allowed_tenants=rule.allowed_tenants,
        )
        capability = next((item for item in self.list(identity) if item.name == name), None)
        if capability is None:
            raise MCPCapabilityNotFoundError("MCP tool is unavailable")
        return capability, rule


__all__ = ["MCPCapabilityExporter", "MCPExportRule"]
