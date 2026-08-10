from __future__ import annotations

from pathlib import Path

import pytest

from copilot.bootstrap.container import build_workflow_container
from copilot.config import Settings
from copilot.contracts import JsonObject, MCPInvocationStatus
from copilot.mcp.server.authorization import MCPServerAuthorization
from copilot.mcp.server.capability_exporter import MCPCapabilityExporter, MCPExportRule
from copilot.mcp.server.tool_provider import MCPToolProvider
from copilot.persistence.mcp_session_repository import MCPInvocationRepository
from tests.mcp_helpers import identity

pytestmark = pytest.mark.integration


def _arguments() -> JsonObject:
    return JsonObject(
        {
            "query": "supplier defect policy",
            "tenant_id": "tenant-alpha",
            "collection_ids": ["quality.v1"],
            "supplier_ids": ["SUP-001"],
            "date_range": {"start": "2026-04-01", "end": "2026-06-30"},
            "top_k": 5,
            "index_snapshot_id": "quality-policy-v1",
        }
    )


def _metadata() -> JsonObject:
    return JsonObject(
        {
            "copilot": {
                "connection_id": "external-client-connection",
                "session_id": "external-client-session",
                "task_id": "TASK-MCP-EXPORT",
                "trace_id": "TRACE-MCP-EXPORT",
                "step_id": "STEP-MCP-EXPORT",
                "tool_call_id": "CALL-MCP-EXPORT",
                "tenant_id": "tenant-alpha",
                "user_id": "quality-analyst",
            }
        }
    )


def test_export_provider_preserves_existing_policy_executor_evidence_and_audit(
    tmp_path: Path,
) -> None:
    settings = Settings(
        app_env="test",
        database_url="sqlite:///unused.db",
        checkpoint_enabled=False,
        artifact_dir=tmp_path / "artifacts",
    )
    with build_workflow_container(settings) as workflow:
        repository = MCPInvocationRepository()
        exporter = MCPCapabilityExporter(
            registry=workflow.registry,
            rules=(MCPExportRule(tool_name="knowledge_search", allowed_tenants=("tenant-alpha",)),),
            authorization=MCPServerAuthorization(),
            server_id="copilot-mcp-server",
            namespace="copilot",
        )
        provider = MCPToolProvider(
            registry=workflow.registry,
            executor=workflow.executor,
            exporter=exporter,
            invocation_repository=repository,
        )
        result = provider.invoke("knowledge_search", _arguments(), identity(), _metadata())

        assert result.status is MCPInvocationStatus.SUCCESS
        assert result.output is not None and result.output.root["match_count"] == 2
        assert len(result.evidence_ids) == 2
        evidence = workflow.evidence.list("TASK-MCP-EXPORT", tenant_id="tenant-alpha")
        assert {item.evidence_id for item in evidence} == set(result.evidence_ids)
        audit = workflow.tool_audit.list(tenant_id="tenant-alpha", task_id="TASK-MCP-EXPORT")
        assert audit[0].tool_name == "knowledge_search"
        assert audit[0].policy_decision == "ALLOW"
        metadata = repository.list(tenant_id="tenant-alpha", task_id="TASK-MCP-EXPORT")
        assert metadata[0].evidence_ids == result.evidence_ids
        assert metadata[0].server_id == "copilot-mcp-server"


def test_export_provider_requires_exact_approval_when_rule_is_gated(tmp_path: Path) -> None:
    settings = Settings(
        app_env="test",
        database_url="sqlite:///unused.db",
        checkpoint_enabled=False,
        artifact_dir=tmp_path / "artifacts",
    )
    with build_workflow_container(settings) as workflow:
        exporter = MCPCapabilityExporter(
            registry=workflow.registry,
            rules=(
                MCPExportRule(
                    tool_name="knowledge_search",
                    allowed_tenants=("tenant-alpha",),
                    require_approval=True,
                ),
            ),
            authorization=MCPServerAuthorization(),
            server_id="copilot-mcp-server",
            namespace="copilot",
        )
        provider = MCPToolProvider(
            registry=workflow.registry,
            executor=workflow.executor,
            exporter=exporter,
            invocation_repository=MCPInvocationRepository(),
        )
        result = provider.invoke("knowledge_search", _arguments(), identity(), _metadata())
        assert result.status is MCPInvocationStatus.PERMISSION_DENIED
        assert result.error is not None and result.error.error_code == "APPROVAL_REQUIRED"
