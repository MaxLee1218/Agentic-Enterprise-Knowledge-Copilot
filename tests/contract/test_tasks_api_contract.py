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
    return TestClient(create_app(task_service=container.task_service, settings=settings)), container


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
