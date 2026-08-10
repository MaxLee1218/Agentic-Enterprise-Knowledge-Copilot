"""MCP client lifecycle manager; it never executes business tools directly."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock

from copilot.contracts import MCPClientIdentity, MCPConnection, MCPSession
from copilot.mcp.client.capability_importer import MCPCapabilityImporter
from copilot.mcp.client.connection_registry import MCPConnectionRegistry
from copilot.mcp.client.session import MCPClientSession
from copilot.mcp.errors import MCPConfigurationError
from copilot.mcp.protocol import MCPProtocolClient
from copilot.mcp.security.connection_policy import MCPConnectionPolicy
from copilot.mcp.security.credential_provider import CredentialProvider
from copilot.persistence.mcp_connection_repository import MCPConnectionRepository
from copilot.persistence.mcp_session_repository import (
    MCPInvocationRepository,
    MCPSessionRepository,
)
from copilot.policies.mcp_access import MCPAccessPolicy
from copilot.services.observability import NoopObservability, ObservabilityPort
from copilot.tools.registry import ToolRegistry


class MCPClientManager:
    """Register, connect, disconnect, reconnect, refresh, revoke, and inspect health."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        connection_registry: MCPConnectionRegistry,
        connection_policy: MCPConnectionPolicy,
        access_policy: MCPAccessPolicy,
        credential_provider: CredentialProvider,
        connection_repository: MCPConnectionRepository,
        session_repository: MCPSessionRepository,
        invocation_repository: MCPInvocationRepository,
        observability: ObservabilityPort | None = None,
        protocol_factory: Callable[[MCPConnection], MCPProtocolClient] = MCPProtocolClient,
    ) -> None:
        self._registry = registry
        self._connections = connection_registry
        self._connection_policy = connection_policy
        self._access_policy = access_policy
        self._credentials = credential_provider
        self._connection_repository = connection_repository
        self._session_repository = session_repository
        self._invocation_repository = invocation_repository
        self._observability = observability or NoopObservability()
        self._protocol_factory = protocol_factory
        self._importer = MCPCapabilityImporter(registry)
        self._sessions: dict[tuple[str, str], MCPClientSession] = {}
        self._lock = RLock()

    def register_connection(self, connection: MCPConnection, *, tenant_id: str) -> None:
        """Validate configuration and persist no secret material."""
        validated = self._connection_policy.validate(connection, tenant_id=tenant_id)
        if validated is not None:
            connection = connection.model_copy(update={"endpoint": validated.canonical_endpoint})
        self._connections.register(connection)
        self._registry.approve_namespace(connection.namespace)
        self._connection_repository.save(connection, tenant_id=tenant_id)

    def connect(self, connection_id: str, identity: MCPClientIdentity) -> MCPSession:
        connection = self._connections.get(connection_id)
        validated = self._connection_policy.validate(connection, tenant_id=identity.tenant_id)
        if validated is not None:
            connection = connection.model_copy(update={"endpoint": validated.canonical_endpoint})
        key = (connection_id, identity.tenant_id)
        with self._lock:
            if key in self._sessions:
                raise MCPConfigurationError("MCP connection already has a tenant session")
            if any(owner_connection == connection_id for owner_connection, _ in self._sessions):
                raise MCPConfigurationError(
                    "MCP connection namespace is already bound to another active tenant"
                )
            session = MCPClientSession(
                connection=connection,
                identity=identity,
                registry=self._registry,
                importer=self._importer,
                policy=self._access_policy,
                credential_provider=self._credentials,
                connection_repository=self._connection_repository,
                session_repository=self._session_repository,
                invocation_repository=self._invocation_repository,
                protocol_factory=self._protocol_factory,
                observability=self._observability,
            )
            self._sessions[key] = session
        try:
            return session.connect()
        except Exception:
            with self._lock:
                self._sessions.pop(key, None)
            raise

    def disconnect(self, connection_id: str, *, tenant_id: str) -> None:
        key = (connection_id, tenant_id)
        with self._lock:
            try:
                session = self._sessions.pop(key)
            except KeyError as exc:
                raise MCPConfigurationError("MCP tenant session is not connected") from exc
        session.close()

    def reconnect(self, connection_id: str, *, tenant_id: str) -> MCPSession:
        session = self._session(connection_id, tenant_id)
        return session.reconnect()

    def refresh_capabilities(self, connection_id: str, *, tenant_id: str) -> MCPSession:
        """Refresh through full reauthorization/reconnect so revoked handles disappear."""
        return self.reconnect(connection_id, tenant_id=tenant_id)

    def revoke(self, connection_id: str) -> None:
        with self._lock:
            keys = tuple(key for key in self._sessions if key[0] == connection_id)
        for _, tenant_id in keys:
            self.disconnect(connection_id, tenant_id=tenant_id)
        connection = self._connections.unregister(connection_id)
        self._registry.revoke_namespace(connection.namespace)

    def health(self) -> tuple[dict[str, str], ...]:
        with self._lock:
            sessions = tuple(self._sessions.items())
        return tuple(
            {
                "connection_id": connection_id,
                "tenant_id": tenant_id,
                "state": session.state.value,
            }
            for (connection_id, tenant_id), session in sorted(sessions)
        )

    def close(self) -> None:
        with self._lock:
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()

    def _session(self, connection_id: str, tenant_id: str) -> MCPClientSession:
        with self._lock:
            try:
                return self._sessions[(connection_id, tenant_id)]
            except KeyError as exc:
                raise MCPConfigurationError("MCP tenant session is not connected") from exc


__all__ = ["MCPClientManager"]
