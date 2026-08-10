"""Policy-gated MCP sampling callback; disabled by omission in the default client."""

from __future__ import annotations

from collections.abc import Callable

from copilot.contracts import JsonObject, MCPCapabilityType, MCPClientIdentity
from copilot.mcp.errors import MCPAuthorizationError
from copilot.policies.mcp_access import MCPAccessPolicy


class MCPSamplingHandler:
    def __init__(
        self,
        *,
        policy: MCPAccessPolicy,
        connection_id: str,
        server_id: str,
        namespace: str,
        identity: MCPClientIdentity,
        sampler: Callable[[JsonObject], JsonObject],
    ) -> None:
        self._policy = policy
        self._connection_id = connection_id
        self._server_id = server_id
        self._namespace = namespace
        self._identity = identity
        self._sampler = sampler

    def __call__(self, request: JsonObject) -> JsonObject:
        decision = self._policy.evaluate_capability(
            connection_id=self._connection_id,
            server_id=self._server_id,
            namespace=self._namespace,
            capability_name="sampling",
            capability_type=MCPCapabilityType.SAMPLING,
            identity=self._identity,
        )
        if not decision.allowed or decision.requires_approval:
            raise MCPAuthorizationError("MCP sampling request is not authorized")
        return self._sampler(request)


__all__ = ["MCPSamplingHandler"]
