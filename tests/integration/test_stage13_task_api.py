"""Stage 13 task-management API integration coverage."""

from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from copilot.api.app import create_app
from copilot.api.dependencies import get_caller_context, get_task_service
from copilot.bootstrap.container import WorkflowContainer, build_workflow_container
from copilot.config import Settings
from copilot.contracts import SpanKind, SpanStatus, TaskPlan
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.persistence.identifiers import SequentialIdentifierFactory
from copilot.security.identity import DemoIdentityProvider
from copilot.services.task_intake import TrustedCallerContext
from tests.workflow_helpers import fixed_clock

TASK_TEXT = "Analyze Q2 2026 supplier quality and generate a JSON report."


class _TaskLocalStepIdOfflineMock(OfflineMockLLM):
    @staticmethod
    def _plan(payload: dict[str, object], node_name: str) -> TaskPlan:
        plan = OfflineMockLLM._plan(payload, node_name)
        identifiers = {
            "knowledge_search": "step-1-knowledge-search",
            "database_query": "step-2-database-query",
            "analysis_engine": "step-3-analysis",
            "report_generator": "step-4-report",
        }
        prior_ids = {step.step_id: identifiers[step.tool_name] for step in plan.steps}
        return plan.model_copy(
            update={
                "steps": tuple(
                    step.model_copy(
                        update={
                            "step_id": identifiers[step.tool_name],
                            "dependency": tuple(prior_ids[item] for item in step.dependency),
                        }
                    )
                    for step in plan.steps
                )
            }
        )


def _client(
    tmp_path: Path,
    *,
    llm_provider: OfflineMockLLM | None = None,
    persistent: bool = False,
) -> tuple[TestClient, WorkflowContainer]:
    settings = Settings(
        app_env="test",
        database_url="sqlite:///unused-stage13-api.db",
        artifact_dir=tmp_path / "artifacts",
        checkpoint_database_path=tmp_path / "workflow.db",
        checkpoint_enabled=persistent,
    )
    container = build_workflow_container(
        settings,
        ids=SequentialIdentifierFactory(),
        clock=fixed_clock,
        sleeper=lambda _seconds: None,
        llm_provider=llm_provider or OfflineMockLLM(),
    )
    application = create_app(
        task_service=container.task_service,
        artifact_service=container.artifact_service,
        approval_service=container.approval_service,
        settings=settings,
        observability=container.observability,
        identity_provider=DemoIdentityProvider(settings),
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
        calculation = next(
            item for item in evidence.json()["evidence"] if item["type"] == "CALCULATION"
        )
        assert calculation["formula"]
        assert calculation["input_evidence_ids"]
        assert "location" not in artifact
        assert not Path(artifact["filename"]).is_absolute()
        assert downloaded.status_code == 200
        assert downloaded.headers["content-type"] == artifact["media_type"]
        assert downloaded.headers["x-artifact-id"] == artifact["artifact_id"]
        assert downloaded.content
    finally:
        container.close()


def test_task_history_is_owner_scoped_filtered_and_paginated(tmp_path: Path) -> None:
    client, container = _client(tmp_path, persistent=True)
    try:
        owner = TrustedCallerContext(
            user_id="U-OWNER",
            tenant_id="TENANT-DEMO",
            data_scope=("quality.v1", "supplier-quality-policy-v1"),
            roles=("quality_analyst",),
            is_demo_identity=False,
        )
        other = owner.model_copy(update={"user_id": "U-OTHER"})
        application = cast(FastAPI, client.app)
        with client:
            application.dependency_overrides[get_caller_context] = lambda: owner
            first = client.post("/v1/tasks", json={"task": TASK_TEXT})
            second = client.post(
                "/v1/tasks",
                json={"task": TASK_TEXT, "require_approval": True},
            )
            application.dependency_overrides[get_caller_context] = lambda: other
            hidden = client.post("/v1/tasks", json={"task": TASK_TEXT})
            application.dependency_overrides[get_caller_context] = lambda: owner

            page = client.get("/v1/tasks", params={"limit": 1, "offset": 0})
            waiting = client.get(
                "/v1/tasks",
                params={"status": "WAITING_APPROVAL", "limit": 20, "offset": 0},
            )
            invalid = client.get("/v1/tasks", params={"limit": 101})

        assert first.status_code == hidden.status_code == 201
        assert second.status_code == 202
        assert page.status_code == 200
        assert page.json()["total"] == 2
        assert page.json()["limit"] == 1
        assert len(page.json()["items"]) == 1
        assert page.json()["items"][0]["task_id"] == second.json()["task_id"]
        assert waiting.status_code == 200
        assert waiting.json()["total"] == 1
        assert waiting.json()["items"][0]["status"] == "WAITING_APPROVAL"
        assert invalid.status_code == 422
        assert invalid.json()["error_code"] == "INVALID_TASK_INPUT"
        assert hidden.json()["task_id"] not in {item["task_id"] for item in page.json()["items"]}
    finally:
        container.close()


def test_two_sequential_tasks_reuse_frozen_step_ids_without_cross_task_reads(
    tmp_path: Path,
) -> None:
    client, container = _client(tmp_path, llm_provider=_TaskLocalStepIdOfflineMock())
    try:
        with client:
            first = client.post(
                "/v1/tasks",
                json={"task": TASK_TEXT, "output_format": "json"},
            )
            second = client.post(
                "/v1/tasks",
                json={
                    "task": "Analyze Q2 2026 supplier quality and generate a PDF report.",
                    "output_format": "pdf",
                },
            )
            assert first.status_code == 201
            assert second.status_code == 201
            first_task_id = first.json()["task_id"]
            second_task_id = second.json()["task_id"]
            assert first_task_id != second_task_id

            first_task = client.get(f"/v1/tasks/{first_task_id}")
            second_task = client.get(f"/v1/tasks/{second_task_id}")
            first_steps = client.get(f"/v1/tasks/{first_task_id}/steps").json()["steps"]
            second_steps = client.get(f"/v1/tasks/{second_task_id}/steps").json()["steps"]
            first_evidence = client.get(f"/v1/tasks/{first_task_id}/evidence").json()["evidence"]
            second_evidence = client.get(f"/v1/tasks/{second_task_id}/evidence").json()["evidence"]
            first_artifacts = client.get(f"/v1/tasks/{first_task_id}/artifacts").json()["artifacts"]
            second_artifacts = client.get(f"/v1/tasks/{second_task_id}/artifacts").json()[
                "artifacts"
            ]

        first_step_ids = {item["step_id"] for item in first_steps}
        second_step_ids = {item["step_id"] for item in second_steps}
        assert first_task.json()["status"] == second_task.json()["status"] == "COMPLETED"
        assert (
            first_step_ids
            == second_step_ids
            == {
                "step-1-knowledge-search",
                "step-2-database-query",
                "step-3-analysis",
                "step-4-report",
            }
        )
        assert all(item["status"] == "SUCCESS" for item in (*first_steps, *second_steps))
        assert {item["step_id"] for item in first_evidence}.issubset(first_step_ids)
        assert {item["step_id"] for item in second_evidence}.issubset(second_step_ids)
        assert {item["evidence_id"] for item in first_evidence}.isdisjoint(
            item["evidence_id"] for item in second_evidence
        )
        assert len(first_artifacts) == len(second_artifacts) == 1
        assert first_artifacts[0]["task_id"] == first_task_id
        assert second_artifacts[0]["task_id"] == second_task_id
        assert first_artifacts[0]["artifact_id"] != second_artifacts[0]["artifact_id"]
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
        audit_events = {
            record.event for record in container.workflow_audit.list(tenant_id="TENANT-DEMO")
        }
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
        audit_events = {
            record.event for record in container.workflow_audit.list(tenant_id="TENANT-DEMO")
        }
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
            metadata = container.artifacts.get_by_id(artifact_id, tenant_id="TENANT-DEMO")
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
