"""Composition root for optional Stage 18 MCP client/server interoperability."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from copilot.bootstrap.container import WorkflowContainer, build_workflow_container
from copilot.config import Settings
from copilot.contracts import MCPClientIdentity, MCPConnection, MCPTransport
from copilot.mcp.client.connection_registry import MCPConnectionRegistry
from copilot.mcp.client.manager import MCPClientManager
from copilot.mcp.protocol import MCPProtocolServer
from copilot.mcp.security.connection_policy import MCPConnectionPolicy
from copilot.mcp.security.credential_provider import EnvCredentialProvider
from copilot.mcp.security.origin_validator import MCPOriginValidator
from copilot.mcp.server.authorization import (
    JWTAuthorizationVerifier,
    MCPServerAuthorization,
)
from copilot.mcp.server.capability_exporter import MCPCapabilityExporter, MCPExportRule
from copilot.mcp.server.prompt_provider import MCPPromptProvider
from copilot.mcp.server.resource_provider import MCPResourceProvider
from copilot.mcp.server.server import MCPServerApplication
from copilot.mcp.server.tool_provider import MCPToolProvider
from copilot.persistence.mcp_connection_repository import MCPConnectionRepository
from copilot.persistence.mcp_session_repository import (
    MCPInvocationRepository,
    MCPSessionRepository,
)
from copilot.policies.mcp_access import MCPAccessPolicy, MCPAccessRule


@dataclass(slots=True)
class MCPContainer:
    """Owned optional MCP dependencies plus the unchanged Stage 0-17 workflow container."""

    workflow: WorkflowContainer
    access_policy: MCPAccessPolicy
    client_manager: MCPClientManager | None
    protocol_server: MCPProtocolServer | None
    connection_repository: MCPConnectionRepository
    session_repository: MCPSessionRepository
    invocation_repository: MCPInvocationRepository

    def close(self) -> None:
        if self.client_manager is not None:
            self.client_manager.close()
        self.connection_repository.close()
        self.session_repository.close()
        self.invocation_repository.close()
        self.workflow.close()

    def __enter__(self) -> MCPContainer:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def build_mcp_container(
    settings: Settings,
    *,
    connections: tuple[MCPConnection, ...] = (),
    access_rules: tuple[MCPAccessRule, ...] = (),
    export_rules: tuple[MCPExportRule, ...] = (),
    stdio_server_identity: MCPClientIdentity | None = None,
) -> MCPContainer:
    """Inject existing registry/executor/policy/evidence/audit/observability into MCP adapters."""
    if not settings.mcp_enabled:
        raise ValueError("MCP is disabled by configuration")
    access_policy = MCPAccessPolicy(access_rules)
    workflow = build_workflow_container(settings, mcp_access_policy=access_policy)
    database = workflow.persistence_database
    connection_repository = MCPConnectionRepository(database, initialize_schema=False)
    session_repository = MCPSessionRepository(database, initialize_schema=False)
    invocation_repository = MCPInvocationRepository(database, initialize_schema=False)
    connection_registry = MCPConnectionRegistry()
    approved_hosts = tuple(
        sorted(
            {
                parts.hostname
                for connection in connections
                if connection.endpoint is not None
                and (parts := urlsplit(connection.endpoint)).hostname is not None
            }
        )
    )
    origin_validator = MCPOriginValidator(
        approved_hosts=approved_hosts,
        allow_private_https=True,
    )
    connection_policy = MCPConnectionPolicy(
        approved_server_ids=tuple(item.server.server_id for item in connections),
        approved_namespaces=tuple(item.namespace for item in connections),
        approved_executables=tuple(
            Path(item.stdio.executable)
            for item in connections
            if item.transport is MCPTransport.STDIO and item.stdio is not None
        ),
        approved_working_directories=tuple(
            Path(item.stdio.working_directory)
            for item in connections
            if item.transport is MCPTransport.STDIO and item.stdio is not None
        ),
        origin_validator=origin_validator,
    )
    client_manager: MCPClientManager | None = None
    if settings.mcp_client_enabled:
        client_manager = MCPClientManager(
            registry=workflow.registry,
            connection_registry=connection_registry,
            connection_policy=connection_policy,
            access_policy=access_policy,
            credential_provider=EnvCredentialProvider(
                allowed_names=settings.mcp_env_credential_names
            ),
            connection_repository=connection_repository,
            session_repository=session_repository,
            invocation_repository=invocation_repository,
            observability=workflow.observability,
        )
        for connection in connections:
            tenant = next(iter(connection.allowed_tenants), "TENANT-UNASSIGNED")
            client_manager.register_connection(connection, tenant_id=tenant)

    protocol_server: MCPProtocolServer | None = None
    if settings.mcp_server_enabled:
        if (
            settings.mcp_jwt_signing_key is None
            or settings.mcp_jwt_issuer is None
            or settings.mcp_jwt_audience is None
        ):
            raise ValueError("MCP server authorization settings are incomplete")
        authorization = MCPServerAuthorization()
        configured_exports = set(settings.mcp_export_allowlist)
        effective_rules = tuple(
            rule for rule in export_rules if rule.tool_name in configured_exports
        )
        exporter = MCPCapabilityExporter(
            registry=workflow.registry,
            rules=effective_rules,
            authorization=authorization,
            server_id="copilot-mcp-server",
            namespace="copilot",
        )
        tools = MCPToolProvider(
            registry=workflow.registry,
            executor=workflow.executor,
            exporter=exporter,
            invocation_repository=invocation_repository,
        )
        dispatch = MCPServerApplication(
            tools=tools,
            resources=MCPResourceProvider(
                resources=(),
                authorization=authorization,
                server_id="copilot-mcp-server",
                namespace="copilot",
            ),
            prompts=MCPPromptProvider(
                prompts=(),
                authorization=authorization,
                server_id="copilot-mcp-server",
                namespace="copilot",
            ),
        )
        protocol_server = MCPProtocolServer(
            dispatch,
            token_verifier=JWTAuthorizationVerifier(
                signing_key=settings.mcp_jwt_signing_key,
                issuer=settings.mcp_jwt_issuer,
                audience=settings.mcp_jwt_audience,
            ),
            stdio_identity=stdio_server_identity,
        )
    return MCPContainer(
        workflow=workflow,
        access_policy=access_policy,
        client_manager=client_manager,
        protocol_server=protocol_server,
        connection_repository=connection_repository,
        session_repository=session_repository,
        invocation_repository=invocation_repository,
    )


__all__ = ["MCPContainer", "build_mcp_container"]
