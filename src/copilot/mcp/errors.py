"""Safe MCP exception taxonomy used outside the official SDK boundary."""

from __future__ import annotations

from copilot.contracts import MCPErrorDetail


class MCPError(RuntimeError):
    """Base typed interoperability error containing no raw transport exception."""

    error_code = "MCP_ERROR"
    recoverable = False

    def __init__(self, message: str, *, recoverable: bool | None = None) -> None:
        safe = " ".join(message.replace("\r", " ").replace("\n", " ").split())[:512]
        super().__init__(safe or "MCP operation failed")
        self.detail = MCPErrorDetail(
            error_code=self.error_code,
            message=safe or "MCP operation failed",
            recoverable=self.recoverable if recoverable is None else recoverable,
        )


class MCPConfigurationError(MCPError):
    error_code = "MCP_CONFIGURATION_ERROR"


class MCPConnectionError(MCPError):
    error_code = "MCP_CONNECTION_ERROR"
    recoverable = True


class MCPProtocolError(MCPError):
    error_code = "MCP_PROTOCOL_ERROR"


class MCPNegotiationError(MCPError):
    error_code = "MCP_NEGOTIATION_ERROR"


class MCPAuthenticationError(MCPError):
    error_code = "MCP_AUTHENTICATION_ERROR"


class MCPAuthorizationError(MCPError):
    error_code = "MCP_AUTHORIZATION_ERROR"


class MCPCapabilityNotFoundError(MCPError):
    error_code = "MCP_CAPABILITY_NOT_FOUND"


class MCPCapabilityRevokedError(MCPError):
    error_code = "MCP_CAPABILITY_REVOKED"


class MCPInvocationError(MCPError):
    error_code = "MCP_INVOCATION_ERROR"


class MCPTimeoutError(MCPError):
    error_code = "MCP_TIMEOUT"
    recoverable = True


class MCPCancelledError(MCPError):
    error_code = "MCP_CANCELLED"


class MCPTransportError(MCPError):
    error_code = "MCP_TRANSPORT_ERROR"
    recoverable = True


class MCPOriginRejectedError(MCPError):
    error_code = "MCP_ORIGIN_REJECTED"


class MCPScopeDeniedError(MCPError):
    error_code = "MCP_SCOPE_DENIED"


class MCPTenantViolationError(MCPError):
    error_code = "MCP_TENANT_VIOLATION"


class MCPSessionExpiredError(MCPError):
    error_code = "MCP_SESSION_EXPIRED"


class MCPRecoveryError(MCPError):
    error_code = "MCP_RECOVERY_ERROR"
    recoverable = True


class MCPInvalidResponseError(MCPError):
    error_code = "MCP_INVALID_RESPONSE"


__all__ = [
    "MCPAuthenticationError",
    "MCPAuthorizationError",
    "MCPCancelledError",
    "MCPCapabilityNotFoundError",
    "MCPCapabilityRevokedError",
    "MCPConfigurationError",
    "MCPConnectionError",
    "MCPError",
    "MCPInvalidResponseError",
    "MCPInvocationError",
    "MCPNegotiationError",
    "MCPOriginRejectedError",
    "MCPProtocolError",
    "MCPRecoveryError",
    "MCPScopeDeniedError",
    "MCPSessionExpiredError",
    "MCPTenantViolationError",
    "MCPTimeoutError",
    "MCPTransportError",
]
