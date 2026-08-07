"""Stage 13 task-management API integration coverage."""

from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from copilot.api.app import create_app
from copilot.api.dependencies import get_caller_context, get_task_service
from copilot.bootstrap.container import WorkflowContainer, build_workflow_container
from copilot.config import Settings
from copilot.contracts import SpanKind, SpanStatus
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.persistence.identifiers import SequentialIdentifierFactory
from copilot.services.task_intake import TrustedCallerContext
from tests.workflow_helpers import fixed_clock

TASK_TEXT = "Analyze Q2 2026 supplier quality and generate a JSON report."


def _client(tmp_path: Path) -> tuple[TestClient, WorkflowContainer]:
    settings = Settings(
        app_env="test",
        database_url="sqlite:///unused-stage13-api.db",
        artifact_dir=tmp_path / "artifacts",
        checkpoint_database_path=tmp_path / "workflow.db",
        checkpoint_enabled=False,
    )
    container = build_workflow_container(
        settings,
        ids=SequentialIdentifierFactory(),
        clock=fixed_clock,
        sleeper=lambda _seconds: None,
        llm_provider=OfflineMockLLM(),
    )
    application = create_app(
        task_service=container.task_service,
        artifact_service=container.artifact_service,
        approval_service=container.approval_service,
        settings=settings,
        observability=container.observability,
    )
    return TestClient(application), container


def test_complete_task_can_be_queried_with_steps_evidence_and_artifact(tmp_path: Path) -> None:
    client, container = _client(tmp_path)
    try:
        with client:
            created = client.post("/v1/tasks", json={"task": TASK_TEXT})
            assert created.status_code == 201
            task_id = created.json()["task_id"]
            task = client.get(f"/v1/tasks/{task_id}")
            steps = client.get(f"/v1/tasks/{task_id}/steps")
            evidence = client.get(f"/v1/tasks/{task_id}/evidence")
            artifacts = client.get(f"/v1/tasks/{task_id}/artifacts")
            artifact = artifacts.json()["artifacts"][0]
            downloaded = client.get(f"/v1/tasks/{task_id}/artifacts/{artifact['artifact_id']}")

        assert task.status_code == 200
        assert task.json()["status"] == "COMPLETED"
        assert task.json()["current_step"] is None
        assert task.json()["step_count"] == 4
        assert task.json()["artifact_count"] == 1
        assert len(steps.json()["steps"]) == 4
        assert all("input" not in item for item in steps.json()["steps"])
        assert evidence.json()["evidence"]
        assert all("content" not in item for item in evidence.json()["evidence"])
        assert "location" not in artifact
        assert not Path(artifact["filename"]).is_absolute()
        assert downloaded.status_code == 200
        assert downloaded.headers["content-type"] == artifact["media_type"]
        assert downloaded.headers["x-artifact-id"] == artifact["artifact_id"]
        assert downloaded.content
    finally:
        container.close()


def test_missing_task_and_terminal_cancellation_use_uniform_errors(tmp_path: Path) -> None:
    client, container = _client(tmp_path)
    try:
        with client:
            missing = client.get("/v1/tasks/T-NOT-FOUND")
            created = client.post("/v1/tasks", json={"task": TASK_TEXT})
            task_id = created.json()["task_id"]
            conflict = client.post(f"/v1/tasks/{task_id}/cancel")
        assert missing.status_code == 404
        assert set(missing.json()) == {
            "error_code",
            "message",
            "task_id",
            "trace_id",
            "details",
        }
        assert missing.json()["error_code"] == "TASK_NOT_FOUND"
        assert conflict.status_code == 409
        assert conflict.json()["error_code"] == "TASK_NOT_CANCELLABLE"
    finally:
        container.close()


def test_waiting_approval_can_be_cancelled_and_old_approval_cannot_resume(
    tmp_path: Path,
) -> None:
    client, container = _client(tmp_path)
    try:
        with client:
            created = client.post(
                "/v1/tasks",
                json={"task": TASK_TEXT, "require_approval": True},
            )
            assert created.status_code == 202
            payload = created.json()
            cancelled = client.post(f"/v1/tasks/{payload['task_id']}/cancel")
            repeated = client.post(f"/v1/tasks/{payload['task_id']}/cancel")
            stale = client.post(
                f"/v1/tasks/{payload['task_id']}/approvals/{payload['pending_approval_id']}",
                json={"action": "approve", "reason": "stale"},
            )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "CANCELLED"
        assert cancelled.json()["cancelled_at"] is not None
        assert repeated.status_code == 200
        assert repeated.json()["status"] == "CANCELLED"
        assert stale.status_code == 409
        assert stale.json()["error_code"] == "APPROVAL_ALREADY_RESOLVED"
        audit_events = {record.event for record in container.workflow_audit.list()}
        assert "task_cancellation_requested" in audit_events
        assert "task_cancelled" in audit_events
    finally:
        container.close()


def test_unauthorized_task_and_artifact_access_is_denied(tmp_path: Path) -> None:
    client, container = _client(tmp_path)
    try:
        with client:
            created = client.post("/v1/tasks", json={"task": TASK_TEXT})
            task_id = created.json()["task_id"]
            artifact_id = created.json()["artifacts"][0]["artifact_id"]
            cast(FastAPI, client.app).dependency_overrides[get_caller_context] = lambda: (
                TrustedCallerContext(
                    user_id="U-OTHER",
                    tenant_id="TENANT-DEMO",
                    data_scope=("quality.v1",),
                )
            )
            task = client.get(f"/v1/tasks/{task_id}")
            artifact = client.get(f"/v1/tasks/{task_id}/artifacts/{artifact_id}")
        assert task.status_code == 403
        assert artifact.status_code == 403
        assert task.json()["error_code"] == "TASK_PERMISSION_DENIED"
        audit_events = {record.event for record in container.workflow_audit.list()}
        assert {"permission_denied", "artifact_read_denied"}.issubset(audit_events)
    finally:
        container.close()


def test_missing_artifact_file_returns_gone_without_exposing_path(tmp_path: Path) -> None:
    client, container = _client(tmp_path)
    try:
        with client:
            created = client.post("/v1/tasks", json={"task": TASK_TEXT})
            payload = created.json()
            artifact_id = payload["artifacts"][0]["artifact_id"]
            metadata = container.artifacts.get_by_id(artifact_id)
            container.artifacts.path_for(metadata).unlink()
            response = client.get(f"/v1/tasks/{payload['task_id']}/artifacts/{artifact_id}")
        assert response.status_code == 410
        assert response.json()["error_code"] == "ARTIFACT_UNAVAILABLE"
        assert str(tmp_path) not in response.text
    finally:
        container.close()


def test_unknown_internal_error_is_safely_normalized(tmp_path: Path) -> None:
    client, container = _client(tmp_path)

    class _BrokenTaskService:
        def get_task(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError(f"private path: {tmp_path}")

    try:
        application = cast(FastAPI, client.app)
        application.dependency_overrides[get_task_service] = lambda: _BrokenTaskService()
        with TestClient(application, raise_server_exceptions=False) as safe_client:
            response = safe_client.get("/v1/tasks/T-001")
        assert response.status_code == 500
        assert response.json()["error_code"] == "INTERNAL_ERROR"
        assert str(tmp_path) not in response.text
    finally:
        container.close()


def test_api_trace_propagates_through_task_graph_steps_and_tools(tmp_path: Path) -> None:
    client, container = _client(tmp_path)
    trace_id = "TRACE-client-stage16"
    try:
        with client:
            response = client.post(
                "/v1/tasks",
                json={"task": TASK_TEXT},
                headers={"X-Trace-ID": trace_id, "X-Request-ID": "REQUEST-client-stage16"},
            )
        assert response.status_code == 201
        assert response.headers["x-trace-id"] == trace_id
        assert response.json()["trace_id"] == trace_id
        assert container.observability is not None
        spans = container.observability.spans_for_trace(trace_id)
        kinds = {span.kind for span in spans}
        assert {
            SpanKind.EXTERNAL_SERVICE,
            SpanKind.TASK,
            SpanKind.GRAPH_NODE,
            SpanKind.STEP,
            SpanKind.TOOL,
        }.issubset(kinds)
        request_span = next(span for span in spans if span.name == "request.http")
        task_span = next(span for span in spans if span.name == "task.total")
        assert task_span.parent_span_id == request_span.span_id
        assert all(span.status is SpanStatus.SUCCEEDED for span in spans)
        assert all(span.step_id for span in spans if span.kind is SpanKind.TOOL)
        summary = container.observability.trace_summary(trace_id, status="COMPLETED")
        assert summary is not None
        assert summary.tool_call_count == 4
        assert summary.step_count == 4
        snapshot = container.observability.metrics_snapshot()
        assert snapshot.counters["tasks_completed_total"] == 1
        assert snapshot.quantiles["task_latency_ms"]["p95"] is not None
    finally:
        container.close()


def test_api_replaces_an_invalid_external_trace_id(tmp_path: Path) -> None:
    client, container = _client(tmp_path)
    try:
        with client:
            response = client.get("/health", headers={"X-Trace-ID": "invalid trace id"})
        generated = response.headers["x-trace-id"]
        assert response.status_code == 200
        assert generated.startswith("TRACE-")
        assert generated != "invalid trace id"
        assert container.observability is not None
        spans = container.observability.spans_for_trace(generated)
        assert len(spans) == 1
        assert spans[0].kind is SpanKind.EXTERNAL_SERVICE
    finally:
        container.close()
