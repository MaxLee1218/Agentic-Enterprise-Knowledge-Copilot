"""Public POST /v1/tasks request, OpenAPI, success, and failure contracts."""

from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from copilot.api.app import create_app
from copilot.bootstrap.container import WorkflowContainer, build_workflow_container
from copilot.config import Settings
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.persistence.identifiers import SequentialIdentifierFactory
from copilot.security.identity import DemoIdentityProvider
from tests.workflow_helpers import fixed_clock


def _client(tmp_path: Path) -> tuple[TestClient, WorkflowContainer]:
    settings = Settings(
        database_url="sqlite:///unused-api-contract.db",
        artifact_dir=tmp_path / "artifacts",
        checkpoint_enabled=False,
    )
    container = build_workflow_container(
        settings,
        ids=SequentialIdentifierFactory(),
        clock=fixed_clock,
        sleeper=lambda _seconds: None,
        llm_provider=OfflineMockLLM(),
    )
    return (
        TestClient(
            create_app(
                task_service=container.task_service,
                settings=settings,
                identity_provider=DemoIdentityProvider(settings),
            )
        ),
        container,
    )


def test_post_tasks_requires_only_task_and_returns_correlation_ids(tmp_path: Path) -> None:
    client, container = _client(tmp_path)
    try:
        with client:
            response = client.post(
                "/v1/tasks",
                json={"task": "Analyze supplier quality in Q2 2026 and generate a JSON report."},
            )
        assert response.status_code == 201
        payload = response.json()
        assert payload["task_id"] == "T-0001"
        assert payload["trace_id"].startswith("TRACE-")
        assert payload["status"] == "COMPLETED"
    finally:
        container.close()


def test_openapi_exposes_no_task_contract_or_plan_input_fields(tmp_path: Path) -> None:
    client, container = _client(tmp_path)
    try:
        schema = cast(FastAPI, client.app).openapi()
        request_schema = schema["components"]["schemas"]["NaturalLanguageTaskSubmission"]
        properties = set(request_schema["properties"])
        assert request_schema["required"] == ["task"]
        assert properties == {
            "task",
            "task_type",
            "output_format",
            "max_steps",
            "read_only",
            "require_approval",
            "session_id",
            "metadata",
        }
        assert not properties.intersection(
            {"goal", "entities", "time_range", "deliverables", "steps", "tool", "arguments"}
        )
        task_type_schema = schema["components"]["schemas"]["TaskType"]
        assert task_type_schema["enum"] == [
            "supplier_quality_analysis.v1",
            "accounts_payable_analysis.v1",
        ]
        approval_path = "/v1/tasks/{task_id}/approvals/{approval_id}"
        assert approval_path in schema["paths"]
        assert set(schema["paths"][approval_path]) == {"get", "post"}
        approval_schema = schema["components"]["schemas"]["ApprovalResolutionRequest"]
        assert set(approval_schema["properties"]) == {
            "action",
            "edited_arguments",
            "reason",
        }
        detail_schema = schema["components"]["schemas"]["ApprovalDetailResponse"]
        assert {"proposed_arguments", "editable_fields", "expires_at"}.issubset(
            detail_schema["properties"]
        )
    finally:
        container.close()


def test_openapi_exposes_all_stage13_routes_and_no_internal_storage_fields(
    tmp_path: Path,
) -> None:
    client, container = _client(tmp_path)
    try:
        schema = cast(FastAPI, client.app).openapi()
        expected = {
            "/v1/tasks": {"get", "post"},
            "/v1/tasks/{task_id}": {"get"},
            "/v1/tasks/{task_id}/steps": {"get"},
            "/v1/tasks/{task_id}/evidence": {"get"},
            "/v1/tasks/{task_id}/artifacts": {"get"},
            "/v1/tasks/{task_id}/artifacts/{artifact_id}": {"get"},
            "/v1/tasks/{task_id}/cancel": {"post"},
        }
        for path, methods in expected.items():
            assert path in schema["paths"]
            assert methods.issubset(schema["paths"][path])
        schemas = schema["components"]["schemas"]
        assert "TaskState" not in schemas
        assert "AgentGraphState" not in schemas
        assert "HTTPValidationError" not in schemas
        for name, component in schemas.items():
            properties = component.get("properties", {})
            assert "location" not in properties, name
            assert "path" not in properties, name
        task_operation = schema["paths"]["/v1/tasks/{task_id}"]["get"]
        assert task_operation["operationId"] == "get_task"
        assert {"200", "403", "404", "500"}.issubset(task_operation["responses"])
        assert task_operation["responses"]["422"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/TaskErrorResponse"
        }
        download = schema["paths"]["/v1/tasks/{task_id}/artifacts/{artifact_id}"]["get"]
        assert "application/octet-stream" in download["responses"]["200"]["content"]
        task_list = schema["paths"]["/v1/tasks"]["get"]
        assert task_list["operationId"] == "list_tasks"
        assert {item["name"] for item in task_list["parameters"]} == {
            "status",
            "limit",
            "offset",
        }
        list_schema = schema["components"]["schemas"]["TaskListResponse"]
        assert set(list_schema["properties"]) == {"items", "total", "limit", "offset"}
    finally:
        container.close()


def test_invalid_task_and_format_use_uniform_error_shape(tmp_path: Path) -> None:
    client, container = _client(tmp_path)
    try:
        with client:
            blank = client.post("/v1/tasks", json={"task": "   "})
            markdown = client.post(
                "/v1/tasks",
                json={"task": "Analyze Q2 2026 supplier quality.", "output_format": "markdown"},
            )
        for response in (blank, markdown):
            assert response.status_code == 422
            assert response.json()["error_code"] == "INVALID_TASK_INPUT"
            assert response.json()["task_id"] is None
            assert response.json()["trace_id"].startswith("TRACE-")
    finally:
        container.close()


def test_caller_can_tighten_approval_without_causing_an_internal_error(
    tmp_path: Path,
) -> None:
    client, container = _client(tmp_path)
    try:
        with client:
            response = client.post(
                "/v1/tasks",
                json={
                    "task": "Analyze Q2 2026 supplier quality and generate a JSON report.",
                    "require_approval": True,
                },
            )
        assert response.status_code == 202
        assert response.json()["status"] == "WAITING_APPROVAL"
        assert response.json()["task_id"] == "T-0001"
    finally:
        container.close()
