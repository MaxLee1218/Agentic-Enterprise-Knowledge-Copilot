"""Isolated governed client session for exactly one external MCP server."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from time import monotonic
from uuid import uuid4

from copilot.contracts import (
    MCPCapabilityType,
    MCPClientIdentity,
    MCPConnection,
    MCPErrorDetail,
    MCPInvocation,
    MCPInvocationContext,
    MCPInvocationMetadata,
    MCPInvocationResult,
    MCPInvocationStatus,
    MCPRecoveryState,
    MCPRecoveryStatus,
    MCPSession,
    MCPSessionState,
    MCPToolCapability,
    SpanKind,
    SpanStatus,
)
from copilot.contracts.validators import utc_now
from copilot.mcp.client.capability_importer import MCPCapabilityImporter
from copilot.mcp.errors import (
    MCPAuthenticationError,
    MCPCancelledError,
    MCPConnectionError,
    MCPError,
    MCPNegotiationError,
    MCPTimeoutError,
)
from copilot.mcp.lifecycle import MCPSessionLifecycle
from copilot.mcp.protocol import MCPProtocolClient, MCPProtocolDiscovery
from copilot.mcp.security.credential_provider import CredentialProvider
from copilot.persistence.mcp_connection_repository import MCPConnectionRepository
from copilot.persistence.mcp_session_repository import (
    MCPInvocationRepository,
    MCPSessionRepository,
)
from copilot.policies.mcp_access import MCPAccessPolicy
from copilot.services.observability import NoopObservability, ObservabilityPort
from copilot.tools.base import ToolExecutionContext
from copilot.tools.registry import ToolRegistry


class MCPClientSession:
    """Own connection, SDK runtime, negotiation, policy, recovery, and registry refresh."""

    def __init__(
        self,
        *,
        connection: MCPConnection,
        identity: MCPClientIdentity,
        registry: ToolRegistry,
        importer: MCPCapabilityImporter,
        policy: MCPAccessPolicy,
        credential_provider: CredentialProvider,
        connection_repository: MCPConnectionRepository,
        session_repository: MCPSessionRepository,
        invocation_repository: MCPInvocationRepository,
        protocol_factory: Callable[[MCPConnection], MCPProtocolClient] = MCPProtocolClient,
        observability: ObservabilityPort | None = None,
        clock: Callable[..., object] = utc_now,
        session_ttl_seconds: int = 3600,
    ) -> None:
        self._connection = connection
        self._identity = identity
        self._registry = registry
        self._importer = importer
        self._policy = policy
        self._credentials = credential_provider
        self._connection_repository = connection_repository
        self._session_repository = session_repository
        self._invocation_repository = invocation_repository
        self._protocol_factory = protocol_factory
        self._observability = observability or NoopObservability()
        self._clock = clock
        self._session_ttl = session_ttl_seconds
        self._lifecycle = MCPSessionLifecycle(observer=self._observe_transition)
        self._protocol: MCPProtocolClient | None = None
        self._discovery: MCPProtocolDiscovery | None = None
        self._reconnect_count = 0

    @property
    def connection(self) -> MCPConnection:
        return self._connection

    @property
    def state(self) -> MCPSessionState:
        return self._lifecycle.state

    @property
    def discovery(self) -> MCPProtocolDiscovery | None:
        return self._discovery

    def connect(self) -> MCPSession:
        """Authorize, initialize, negotiate, import, persist, and become READY."""
        decision = self._policy.evaluate_connection(
            connection_id=self._connection.connection_id,
            server_id=self._connection.server.server_id,
            namespace=self._connection.namespace,
            identity=self._identity,
        )
        if not decision.allowed:
            self._observability.increment("mcp_policy_denied_count")
            raise MCPAuthenticationError("MCP connection policy denied the caller")
        self._lifecycle.transition(MCPSessionState.CONNECTING)
        self._observability.increment("mcp_connection_count")
        self._observability.gauge_add("mcp_active_sessions", 1)
        started = monotonic()
        try:
            self._lifecycle.transition(MCPSessionState.INITIALIZING)
            secret = self._credentials.resolve(self._connection.credential_reference)
            protocol = self._protocol_factory(self._connection)
            self._protocol = protocol
            self._lifecycle.transition(MCPSessionState.NEGOTIATING)
            discovery = protocol.connect(
                credential=secret.get_secret_value() if secret is not None else None
            )
            if discovery.server_id != self._connection.server.server_id:
                raise MCPNegotiationError("MCP server identity does not match configuration")
            self._discovery = discovery
            tools = tuple(
                item
                for item in discovery.negotiated.capabilities
                if isinstance(item, MCPToolCapability)
                and self._policy.evaluate_capability(
                    connection_id=self._connection.connection_id,
                    server_id=self._connection.server.server_id,
                    namespace=self._connection.namespace,
                    capability_name=item.name,
                    capability_type=MCPCapabilityType.TOOL,
                    identity=self._identity,
                ).allowed
            )
            self._registry.approve_namespace(self._connection.namespace)
            self._importer.refresh(
                namespace=self._connection.namespace,
                capabilities=tools,
                session=self,
            )
            self._lifecycle.transition(MCPSessionState.READY)
            snapshot = self._snapshot()
            self._connection_repository.save(self._connection, tenant_id=self._identity.tenant_id)
            self._session_repository.save(snapshot)
            self._observability.gauge_add("mcp_capability_count", len(tools))
            self._observability.observe(
                "mcp_latency",
                max(0.0, (monotonic() - started) * 1000),
                labels={"operation": "connect"},
            )
            return snapshot
        except Exception as exc:
            self._observability.increment("mcp_connection_failure_count")
            self._lifecycle.transition(MCPSessionState.FAILED, reason=type(exc).__name__)
            self._observability.gauge_add("mcp_active_sessions", -1)
            if self._protocol is not None:
                self._protocol.close()
                self._protocol = None
            raise

    def invoke(
        self,
        capability: MCPToolCapability,
        arguments: object,
        context: ToolExecutionContext,
    ) -> MCPInvocationResult:
        """Invoke after current policy revalidation; retry only idempotent transport failures."""
        from copilot.contracts import JsonObject

        if not isinstance(arguments, JsonObject):
            raise TypeError("MCP invocation arguments must be JsonObject")
        self._lifecycle.require_ready()
        discovery = self._require_discovery()
        if context.tenant_id != self._identity.tenant_id:
            raise MCPAuthenticationError("MCP invocation tenant does not match its session")
        decision = self._policy.evaluate_capability(
            connection_id=self._connection.connection_id,
            server_id=self._connection.server.server_id,
            namespace=self._connection.namespace,
            capability_name=capability.name,
            capability_type=MCPCapabilityType.TOOL,
            identity=self._identity,
        )
        if not decision.allowed:
            self._observability.increment("mcp_policy_denied_count")
            raise MCPAuthenticationError("MCP capability policy denied the invocation")
        invocation_id = f"MCPINV-{uuid4().hex}"
        invocation_context = MCPInvocationContext(
            connection_id=self._connection.connection_id,
            session_id=discovery.session_id,
            server_id=self._connection.server.server_id,
            namespace=self._connection.namespace,
            capability_name=capability.name,
            capability_type=MCPCapabilityType.TOOL,
            client_identity=self._identity,
            task_id=context.call.task_id,
            trace_id=context.trace_id,
            step_id=context.call.step_id,
            tool_call_id=context.call.tool_call_id,
            deadline_at=context.call.deadline_at,
            approval_id=context.call.approval_id,
        )
        invocation = MCPInvocation(
            invocation_id=invocation_id,
            capability=capability,
            arguments=arguments,
            context=invocation_context,
        )
        started_at = utc_now()
        self._observability.increment("mcp_invocation_count")
        with (
            self._observability.bind_context(
                task_id=context.call.task_id,
                trace_id=context.trace_id,
                step_id=context.call.step_id,
                tool_name=capability.canonical_name,
                tenant_id=context.tenant_id,
                user_id=context.user_id,
                session_id=discovery.session_id,
            ),
            self._observability.span(
                "mcp.invoke",
                SpanKind.EXTERNAL_SERVICE,
                attributes={
                    "transport": self._connection.transport.value,
                    "capability_type": capability.capability_type.value,
                    "namespace": capability.namespace,
                },
            ) as span,
        ):
            result = self._invoke_with_retry(invocation, context)
            if result.status is MCPInvocationStatus.SUCCESS:
                span.set_status(SpanStatus.SUCCEEDED)
            else:
                error_code = result.error.error_code if result.error else None
                span.set_status(SpanStatus.FAILED, error_type=error_code)
                self._observability.increment("mcp_invocation_failure_count")
        self._observability.observe(
            "mcp_latency", result.latency_ms, labels={"operation": "invoke"}
        )
        self._invocation_repository.append(
            MCPInvocationMetadata(
                invocation_id=invocation_id,
                task_id=context.call.task_id,
                trace_id=context.trace_id,
                tenant_id=context.tenant_id,
                user_id=context.user_id,
                client_id=self._identity.client_id,
                server_id=self._connection.server.server_id,
                session_id=discovery.session_id,
                protocol_revision=self._connection.protocol_revision,
                transport=self._connection.transport,
                namespace=self._connection.namespace,
                capability_name=capability.name,
                capability_type=MCPCapabilityType.TOOL,
                policy_decision="ALLOW",
                approval_id=context.call.approval_id,
                origin=capability.origin,
                provenance=capability.provenance,
                latency_ms=result.latency_ms,
                retry_count=result.retry_count,
                outcome=result.status,
                typed_error=result.error.error_code if result.error else None,
                timestamp=started_at,
            )
        )
        return result

    def _invoke_with_retry(
        self, invocation: MCPInvocation, context: ToolExecutionContext
    ) -> MCPInvocationResult:
        protocol = self._require_protocol()
        retry_allowed = self._policy.allows_retry(
            connection_id=self._connection.connection_id,
            idempotent=invocation.capability.idempotent,
            read_only=invocation.capability.read_only,
            destructive=invocation.capability.destructive,
        )
        for retry_count in range(3):
            try:
                result = protocol.invoke(
                    invocation,
                    cancellation_requested=lambda: context.cancellation.cancellation_requested,
                )
                return result.model_copy(update={"retry_count": retry_count})
            except MCPTimeoutError as exc:
                self._observability.increment("mcp_timeout_count")
                if not retry_allowed or retry_count >= 2:
                    return _error_result(
                        invocation.invocation_id,
                        MCPInvocationStatus.TIMEOUT,
                        exc,
                        retry_count=retry_count,
                    )
            except MCPCancelledError as exc:
                return _error_result(
                    invocation.invocation_id,
                    MCPInvocationStatus.CANCELLED,
                    exc,
                    retry_count=retry_count,
                )
            except MCPConnectionError as exc:
                if not retry_allowed or retry_count >= 2:
                    return _error_result(
                        invocation.invocation_id,
                        MCPInvocationStatus.TECHNICAL_FAILURE,
                        exc,
                        retry_count=retry_count,
                    )
            except MCPError as exc:
                return _error_result(
                    invocation.invocation_id,
                    MCPInvocationStatus.TECHNICAL_FAILURE,
                    exc,
                    retry_count=retry_count,
                )
        raise AssertionError("bounded MCP retry loop must return")  # pragma: no cover

    def reconnect(self) -> MCPSession:
        """Re-resolve credentials and reauthorize every tenant/scope/origin/capability."""
        if self.state not in {MCPSessionState.READY, MCPSessionState.FAILED}:
            raise MCPConnectionError("MCP session cannot reconnect from its current state")
        self._lifecycle.transition(MCPSessionState.RECONNECTING)
        self._observability.increment("mcp_reconnect_count")
        self._reconnect_count += 1
        self._registry.revoke_namespace(self._connection.namespace)
        if self._protocol is not None:
            self._protocol.close()
        self._protocol = None
        self._discovery = None
        self._lifecycle.transition(MCPSessionState.CONNECTING)
        # Continue through the same phases without recursively taking the CREATED transition.
        self._lifecycle.transition(MCPSessionState.INITIALIZING)
        decision = self._policy.evaluate_connection(
            connection_id=self._connection.connection_id,
            server_id=self._connection.server.server_id,
            namespace=self._connection.namespace,
            identity=self._identity,
        )
        if not decision.allowed:
            self._lifecycle.transition(MCPSessionState.FAILED)
            raise MCPAuthenticationError("MCP reconnect authorization failed")
        secret = self._credentials.resolve(self._connection.credential_reference)
        protocol = self._protocol_factory(self._connection)
        self._protocol = protocol
        self._lifecycle.transition(MCPSessionState.NEGOTIATING)
        discovery = protocol.connect(
            credential=secret.get_secret_value() if secret is not None else None
        )
        if discovery.server_id != self._connection.server.server_id:
            self._lifecycle.transition(MCPSessionState.FAILED)
            raise MCPNegotiationError("MCP reconnect server identity changed")
        self._discovery = discovery
        tools = tuple(
            item
            for item in discovery.negotiated.capabilities
            if isinstance(item, MCPToolCapability)
            and self._policy.evaluate_capability(
                connection_id=self._connection.connection_id,
                server_id=self._connection.server.server_id,
                namespace=self._connection.namespace,
                capability_name=item.name,
                capability_type=MCPCapabilityType.TOOL,
                identity=self._identity,
            ).allowed
        )
        self._importer.refresh(
            namespace=self._connection.namespace,
            capabilities=tools,
            session=self,
        )
        self._lifecycle.transition(MCPSessionState.READY)
        snapshot = self._snapshot()
        self._session_repository.save(snapshot)
        return snapshot

    def close(self) -> None:
        if self.state is MCPSessionState.CLOSED:
            return
        if self.state is MCPSessionState.CREATED:
            self._lifecycle.transition(MCPSessionState.CLOSED)
            return
        if self.state is MCPSessionState.EXPIRED:
            self._lifecycle.transition(MCPSessionState.CLOSED)
        else:
            self._lifecycle.transition(MCPSessionState.DISCONNECTING)
            self._registry.revoke_namespace(self._connection.namespace)
            if self._protocol is not None:
                self._protocol.close()
            self._protocol = None
            self._lifecycle.transition(MCPSessionState.CLOSED)
        self._observability.gauge_add("mcp_active_sessions", -1)
        self._session_repository.save(self._snapshot())

    def _snapshot(self) -> MCPSession:
        now = utc_now()
        discovery = self._discovery
        session_id = (
            discovery.session_id
            if discovery is not None
            else f"MCP-PENDING-{self._connection.connection_id}"
        )
        return MCPSession(
            session_id=session_id,
            connection_id=self._connection.connection_id,
            server_id=self._connection.server.server_id,
            tenant_id=self._identity.tenant_id,
            state=self.state,
            protocol_revision=self._connection.protocol_revision,
            transport=self._connection.transport,
            namespace=self._connection.namespace,
            client_identity=self._identity,
            negotiated=discovery.negotiated if discovery is not None else None,
            recovery=MCPRecoveryState(
                status=(
                    MCPRecoveryStatus.RESTORED if self._reconnect_count else MCPRecoveryStatus.NONE
                ),
                reconnect_count=self._reconnect_count,
            ),
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=self._session_ttl),
        )

    def _require_protocol(self) -> MCPProtocolClient:
        if self._protocol is None:
            raise MCPConnectionError("MCP protocol client is unavailable")
        return self._protocol

    def _require_discovery(self) -> MCPProtocolDiscovery:
        if self._discovery is None:
            raise MCPConnectionError("MCP negotiation is unavailable")
        return self._discovery

    def _observe_transition(self, event: object) -> None:
        self._observability.emit(
            "mcp.lifecycle",
            level=logging.INFO,
            fields={"state": self.state.value},
        )


def _error_result(
    invocation_id: str,
    status: MCPInvocationStatus,
    error: MCPError,
    *,
    retry_count: int,
) -> MCPInvocationResult:
    now = utc_now()
    return MCPInvocationResult(
        invocation_id=invocation_id,
        status=status,
        error=MCPErrorDetail(
            error_code=error.detail.error_code,
            message=error.detail.message,
            recoverable=error.detail.recoverable,
        ),
        started_at=now,
        completed_at=now,
        retry_count=retry_count,
    )


__all__ = ["MCPClientSession"]
