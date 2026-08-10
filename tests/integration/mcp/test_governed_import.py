from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from copilot.bootstrap.mcp import build_mcp_container
from copilot.config import Settings
from copilot.contracts import JsonObject, ToolCall, ToolResultStatus
from copilot.contracts.validators import utc_now
from copilot.mcp.errors import MCPConfigurationError
from copilot.policies.mcp_access import MCPAccessRule
from copilot.services.execution import ExecutionContext
from copilot.tools.cancellation import CancellationToken
from tests.mcp_helpers import identity, stdio_connection

pytestmark = pytest.mark.integration


def test_imported_real_tool_uses_existing_executor_evidence_audit_and_recovery(
    tmp_path: Path,
) -> None:
    connection = stdio_connection().model_copy(
        update={"allowed_tenants": ("tenant-alpha", "tenant-beta")}
    )
    policy = MCPAccessRule(
        connection_id=connection.connection_id,
        server_id=connection.server.server_id,
        namespace=connection.namespace,
        tenants=frozenset({"tenant-alpha", "tenant-beta"}),
        capability_names=frozenset({"echo"}),
    )
    settings = Settings(
        app_env="test",
        database_url="sqlite:///unused.db",
        checkpoint_enabled=False,
        artifact_dir=tmp_path / "artifacts",
        mcp_enabled=True,
        mcp_client_enabled=True,
    )
    with build_mcp_container(
        settings, connections=(connection,), access_rules=(policy,)
    ) as container:
        manager = container.client_manager
        assert manager is not None
        snapshot = manager.connect(connection.connection_id, identity())
        assert snapshot.state.value == "READY"
        canonical_name = f"{connection.namespace}.echo"
        definition = container.workflow.registry.get(canonical_name).definition
        deadline = utc_now() + timedelta(seconds=30)
        call = ToolCall(
            tool_call_id="CALL-MCP-IMPORT",
            task_id="TASK-MCP-IMPORT",
            step_id="STEP-MCP-IMPORT",
            tool_name=canonical_name,
            tool_version=definition.tool_version,
            input=JsonObject({"text": "governed-import"}),
            idempotency_key="IDEMPOTENCY-MCP-IMPORT",
            deadline_at=deadline,
            tenant_id="tenant-alpha",
            user_id="quality-analyst",
        )
        result = container.workflow.executor.execute(
            call,
            ExecutionContext(
                task_id=call.task_id,
                trace_id="TRACE-MCP-IMPORT",
                step_id=call.step_id,
                user_id=call.user_id,
                tenant_id=call.tenant_id,
                roles=("quality_analyst",),
                scopes=("mcp.tools.invoke",),
                data_scope=("supplier_quality",),
                purpose="supplier_quality_analysis.v1",
                authentication_source="hermetic_test",
                is_demo_identity=False,
                authenticated=True,
                deadline_at=deadline,
                approval_required=False,
                approval_id=None,
                cancellation=CancellationToken(),
            ),
        )
        assert result.status is ToolResultStatus.SUCCESS
        assert result.output == JsonObject({"echoed": "governed-import"})
        assert len(result.evidence_ids) == 1
        evidence = container.workflow.evidence.list(call.task_id, tenant_id=call.tenant_id)
        assert (
            evidence[0].source_reference.reference.root["server_id"] == connection.server.server_id
        )
        audit = container.workflow.tool_audit.list(tenant_id=call.tenant_id, task_id=call.task_id)
        assert audit[0].tool_name == canonical_name
        assert audit[0].tool_origin == f"mcp_server:{connection.server.server_id}"
        invocations = container.invocation_repository.list(
            tenant_id=call.tenant_id, task_id=call.task_id
        )
        assert invocations[0].capability_name == "echo"
        assert invocations[0].protocol_revision.value == "2025-11-25"

        with pytest.raises(MCPConfigurationError, match="another active tenant"):
            manager.connect(connection.connection_id, identity(tenant_id="tenant-beta"))

        restored = manager.reconnect(connection.connection_id, tenant_id=call.tenant_id)
        assert restored.recovery.reconnect_count == 1
        assert container.workflow.registry.contains(canonical_name)
        manager.revoke(connection.connection_id)
        assert not container.workflow.registry.contains(canonical_name)


def test_tenant_scoped_connection_repository_does_not_cross_tenants(tmp_path: Path) -> None:
    from copilot.persistence.mcp_connection_repository import MCPConnectionRepository

    repository = MCPConnectionRepository(tmp_path / "mcp-state.db")
    try:
        connection = stdio_connection()
        repository.save(connection, tenant_id="tenant-alpha")
        assert repository.get(connection.connection_id, tenant_id="tenant-alpha") == connection
        with pytest.raises(KeyError):
            repository.get(connection.connection_id, tenant_id="tenant-beta")
        assert "credential_reference" in connection.model_dump_json()
        assert "access_token" not in connection.model_dump_json().lower()
    finally:
        repository.close()
