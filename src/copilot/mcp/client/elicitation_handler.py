"""Policy-gated elicitation that rejects requests for credentials and secrets."""

from __future__ import annotations

from collections.abc import Callable

from copilot.contracts import JsonObject, MCPCapabilityType, MCPClientIdentity
from copilot.mcp.errors import MCPAuthorizationError, MCPInvalidResponseError
from copilot.policies.mcp_access import MCPAccessPolicy

_FORBIDDEN_FIELDS = ("password", "token", "secret", "credential", "api_key", "private_key")


class MCPElicitationHandler:
    def __init__(
        self,
        *,
        policy: MCPAccessPolicy,
        connection_id: str,
        server_id: str,
        namespace: str,
        identity: MCPClientIdentity,
        responder: Callable[[JsonObject], JsonObject],
    ) -> None:
        self._policy = policy
        self._connection_id = connection_id
        self._server_id = server_id
        self._namespace = namespace
        self._identity = identity
        self._responder = responder

    def __call__(self, request: JsonObject) -> JsonObject:
        decision = self._policy.evaluate_capability(
            connection_id=self._connection_id,
            server_id=self._server_id,
            namespace=self._namespace,
            capability_name="elicitation",
            capability_type=MCPCapabilityType.ELICITATION,
            identity=self._identity,
        )
        if not decision.allowed or decision.requires_approval:
            raise MCPAuthorizationError("MCP elicitation request is not authorized")
        lowered = str(request.root).lower()
        if any(field in lowered for field in _FORBIDDEN_FIELDS):
            raise MCPInvalidResponseError("MCP elicitation cannot request private credentials")
        return self._responder(request)


__all__ = ["MCPElicitationHandler"]
