"""Real API acceptance plus independent PostgreSQL Worker execution integration."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from threading import Event
from time import sleep
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from copilot.api.app import create_app
from copilot.api.dependencies import get_caller_context
from copilot.bootstrap.container import WorkflowContainer, build_application
from copilot.bootstrap.worker import WorkerApplication, build_worker_application
from copilot.config import PROJECT_ROOT, Settings, get_settings
from copilot.contracts import JsonObject, MoneyThreshold, TaskType
from copilot.persistence.checkpoint import migrate_postgres_checkpoints
from copilot.persistence.models import (
    WorkflowLeaseRow,
    WorkflowStepResultRow,
    WorkflowTaskRow,
)
from copilot.security.identity import DemoIdentityProvider
from copilot.services.task_execution import LeaseHeartbeat
from copilot.services.task_intake import TrustedCallerContext
from copilot.tools.base import ToolExecutionContext, ToolExecutionOutput
from copilot.tools.database.ap_seed import seed_accounts_payable_demo_database

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")
TASK_TEXT = "Analyze supplier quality in Q2 2026 and generate a JSON report."
AP_TASK_TEXT = (
    "Analyze all Accounts Payable exceptions from 2026-04-01 to 2026-06-30 "
    "for LE-CN-01 and LE-US-01"
)
AP_MANIFEST_CHECKSUM = "sha256:3095ebb099a2db12dffbc699cf1f65bb7d8e324d025eb701af4bf825d6adab33"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not configured"),
]


@dataclass(slots=True)
class AsyncHarness:
    client: TestClient
    api: WorkflowContainer
    worker: WorkerApplication
    tenant_id: str

    def run_until(self, task_id: str, statuses: set[str]) -> dict[str, object]:
        payload: dict[str, object] = {}
        for _attempt in range(500):
            self.worker.runtime.run_once()
            response = self.client.get(f"/v1/tasks/{task_id}")
            assert response.status_code == 200, response.text
            payload = cast(dict[str, object], response.json())
            stable_runtime = (
                payload["runtime_status"] == "FINISHED"
                if payload["status"] in {"COMPLETED", "FAILED", "CANCELLED"}
                else payload["runtime_status"] == "SUSPENDED"
                if payload["status"] == "WAITING_APPROVAL"
                else True
            )
            if payload["status"] in statuses and stable_runtime:
                return payload
            sleep(0.02)
        raise AssertionError(
            f"Task {task_id} did not reach {sorted(statuses)}; last projection={payload}"
        )


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AsyncHarness]:
    assert POSTGRES_URL is not None
    # The frozen AP policy/data fixtures are tenant-bound to TENANT-DEMO. The disposable
    # PostgreSQL harness is serialized and removes every Task it creates during teardown.
    tenant_id = "TENANT-DEMO"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("PERSISTENCE_DATABASE_URL", POSTGRES_URL)
    get_settings.cache_clear()
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    migrate_postgres_checkpoints(POSTGRES_URL.replace("+psycopg", ""))
    business_database_url = f"sqlite:///{tmp_path / 'business.db'}"
    seed_accounts_payable_demo_database(business_database_url)
    settings = Settings(
        app_env="test",
        database_url=business_database_url,
        persistence_database_url=POSTGRES_URL,
        persistence_auto_create_schema=False,
        artifact_dir=tmp_path / "artifacts",
        checkpoint_enabled=True,
        knowledge_provider="mock",
        database_provider="mock",
        llm_provider="mock",
        demo_tenant_id=tenant_id,
        demo_user_id="U-PG-QUEUE-E2E",
        demo_approval_roles=("quality_analyst", "quality_data_approver"),
        worker_concurrency=2,
        worker_poll_interval_seconds=0.01,
        execution_heartbeat_interval_seconds=1,
        execution_lease_ttl_seconds=5,
        log_level="WARNING",
        log_format="text",
    )
    api = build_application(settings)
    worker = build_worker_application(settings)
    client = TestClient(
        create_app(
            task_service=api.task_service,
            task_submission_service=api.task_submission_service,
            approval_service=api.approval_service,
            artifact_service=api.artifact_service,
            settings=settings,
            observability=api.observability,
            readiness=api.readiness,
            identity_provider=DemoIdentityProvider(settings),
        )
    )
    value = AsyncHarness(client=client, api=api, worker=worker, tenant_id=tenant_id)
    try:
        with client:
            yield value
    finally:
        worker.close()
        api.close()
        database = api.persistence_database
        if database is not None:
            # The API container has disposed its pool, so cleanup through a fresh owned engine.
            from copilot.persistence.database import PersistenceDatabase

            cleanup = PersistenceDatabase(POSTGRES_URL)
            with cleanup.session() as session:
                session.execute(
                    delete(WorkflowTaskRow).where(WorkflowTaskRow.tenant_id == tenant_id)
                )
            cleanup.dispose()
        get_settings.cache_clear()


def test_post_returns_before_graph_and_worker_eventually_completes(
    harness: AsyncHarness,
) -> None:
    submitted = harness.client.post("/v1/tasks", json={"task": TASK_TEXT})
    assert submitted.status_code == 202
    accepted = submitted.json()
    assert accepted["task_status"] == "CREATED"
    assert accepted["runtime_status"] == "READY"
    assert harness.api.engine.checkpoint_identity(accepted["task_id"], harness.tenant_id) is None

    terminal = harness.run_until(accepted["task_id"], {"COMPLETED"})
    assert terminal["runtime_status"] == "FINISHED"
    assert terminal["artifact_count"] == 1
    steps = harness.client.get(f"/v1/tasks/{accepted['task_id']}/steps").json()["steps"]
    evidence = harness.client.get(f"/v1/tasks/{accepted['task_id']}/evidence").json()["evidence"]
    artifacts = harness.client.get(f"/v1/tasks/{accepted['task_id']}/artifacts").json()["artifacts"]
    assert len(steps) == 4
    assert {item["type"] for item in evidence} == {
        "DOCUMENT",
        "DATABASE",
        "CALCULATION",
    }
    assert len(artifacts) == 1
    download = harness.client.get(
        f"/v1/tasks/{accepted['task_id']}/artifacts/{artifacts[0]['artifact_id']}"
    )
    assert download.status_code == 200
    assert f"sha256:{sha256(download.content).hexdigest()}" == artifacts[0]["checksum"]


def test_accounts_payable_full_plan_executes_through_async_worker(
    harness: AsyncHarness,
) -> None:
    owner = TrustedCallerContext(
        user_id="U-FINANCE-ASYNC",
        tenant_id=harness.tenant_id,
        data_scope=("accounts_payable.v1", "accounts-payable-policy-v1"),
        legal_entity_ids=("LE-CN-01", "LE-US-01"),
        currency_scope=("CNY", "USD"),
        allowed_task_types=(TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1,),
        roles=("finance_analyst",),
        scopes=(
            "task:execute",
            "task:read",
            "evidence:read",
            "finance:ap.detail",
            "finance:ap.artifact:download",
            "artifact.write",
        ),
        purpose=TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1.value,
        is_demo_identity=False,
        policy_rule_set_id="accounts-payable-v1",
        policy_rule_set_version="ap_rules.2026.1",
        policy_manifest_checksum=AP_MANIFEST_CHECKSUM,
        policy_materiality=(
            MoneyThreshold(currency="CNY", amount=Decimal("5000")),
            MoneyThreshold(currency="USD", amount=Decimal("1000")),
        ),
        policy_snapshot_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    app = cast(FastAPI, harness.client.app)
    app.dependency_overrides[get_caller_context] = lambda: owner
    try:
        submitted = harness.client.post(
            "/v1/tasks",
            json={
                "task": AP_TASK_TEXT,
                "task_type": "accounts_payable_analysis.v1",
                "output_format": "json",
            },
        )
        assert submitted.status_code == 202, submitted.text
        task_id = submitted.json()["task_id"]
        completed = harness.run_until(task_id, {"COMPLETED"})
        steps = harness.client.get(f"/v1/tasks/{task_id}/steps").json()["steps"]
        evidence = harness.client.get(f"/v1/tasks/{task_id}/evidence").json()["evidence"]
        artifacts = harness.client.get(f"/v1/tasks/{task_id}/artifacts").json()["artifacts"]
    finally:
        app.dependency_overrides.pop(get_caller_context, None)

    assert completed["runtime_status"] == "FINISHED"
    assert len(steps) == 14
    assert {item["type"] for item in evidence} == {
        "DOCUMENT",
        "DATABASE",
        "CALCULATION",
    }
    assert len(artifacts) == 1


def test_approval_releases_worker_and_resolution_redispatches_without_inline_graph(
    harness: AsyncHarness,
) -> None:
    submitted = harness.client.post(
        "/v1/tasks",
        json={"task": TASK_TEXT, "require_approval": True},
    )
    task_id = submitted.json()["task_id"]
    waiting = harness.run_until(task_id, {"WAITING_APPROVAL"})
    approval_id = waiting["pending_approval_id"]
    assert isinstance(approval_id, str)
    assert waiting["runtime_status"] == "SUSPENDED"
    assert harness.api.persistence_database is not None
    with harness.api.persistence_database.session() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(WorkflowLeaseRow)
                .where(
                    WorkflowLeaseRow.tenant_id == harness.tenant_id,
                    WorkflowLeaseRow.task_id == task_id,
                )
            )
            == 0
        )
        successful_before = len(
            tuple(
                session.scalars(
                    select(WorkflowStepResultRow).where(
                        WorkflowStepResultRow.tenant_id == harness.tenant_id,
                        WorkflowStepResultRow.task_id == task_id,
                    )
                )
            )
        )

    resolved = harness.client.post(
        f"/v1/tasks/{task_id}/approvals/{approval_id}",
        json={"action": "approve", "reason": "Authorized for this governed scope."},
    )
    assert resolved.status_code == 202
    assert resolved.json()["task_status"] == "EXECUTING"
    assert resolved.json()["runtime_status"] == "READY"
    immediate = harness.client.get(f"/v1/tasks/{task_id}").json()
    assert immediate["status"] == "EXECUTING"
    assert immediate["runtime_status"] == "READY"

    completed = harness.run_until(task_id, {"COMPLETED"})
    assert completed["runtime_status"] == "FINISHED"
    with harness.api.persistence_database.session() as session:
        successful_after = len(
            tuple(
                session.scalars(
                    select(WorkflowStepResultRow).where(
                        WorkflowStepResultRow.tenant_id == harness.tenant_id,
                        WorkflowStepResultRow.task_id == task_id,
                    )
                )
            )
        )
    assert successful_after >= successful_before


def test_queued_cancellation_is_durable_and_worker_delivery_is_noop(
    harness: AsyncHarness,
) -> None:
    submitted = harness.client.post("/v1/tasks", json={"task": TASK_TEXT})
    task_id = submitted.json()["task_id"]
    cancelled = harness.client.post(f"/v1/tasks/{task_id}/cancel")
    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["runtime_status"] == "FINISHED"

    terminal = harness.run_until(task_id, {"CANCELLED"})
    assert terminal["artifact_count"] == 0


def test_running_cancellation_fences_late_tool_result(
    harness: AsyncHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A durable cancellation wins even when a non-cooperative Tool returns later."""
    started = Event()
    release = Event()
    original_execute = harness.worker.container.database_tool.execute

    def blocking_execute(
        arguments: JsonObject,
        context: ToolExecutionContext,
    ) -> ToolExecutionOutput:
        started.set()
        if not release.wait(timeout=10):
            raise TimeoutError("Test did not release the blocked database Tool")
        return original_execute(arguments, context)

    monkeypatch.setattr(
        harness.worker.container.database_tool,
        "execute",
        blocking_execute,
    )
    submitted = harness.client.post("/v1/tasks", json={"task": TASK_TEXT})
    task_id = submitted.json()["task_id"]
    try:
        assert harness.worker.runtime.run_once() == 1
        assert started.wait(timeout=5), "Worker did not enter the blocked Tool"

        cancelled = harness.client.post(f"/v1/tasks/{task_id}/cancel")
        assert cancelled.status_code == 202
        assert cancelled.json()["status"] == "CANCELLED"
        assert cancelled.json()["runtime_status"] == "FINISHED"

        # The heartbeat observes the deleted lease and signals the process-local Tool token.
        sleep(1.2)
    finally:
        release.set()

    for _attempt in range(250):
        harness.worker.runtime.run_once()
        assert harness.api.async_runtime_repository is not None
        snapshot = harness.api.async_runtime_repository.snapshot(
            task_id,
            tenant_id=harness.tenant_id,
        )
        if (
            snapshot.cancellation is not None
            and snapshot.cancellation.worker_observed_at is not None
        ):
            break
        sleep(0.02)
    else:
        raise AssertionError("Worker did not durably observe cancellation")

    terminal = harness.client.get(f"/v1/tasks/{task_id}").json()
    assert terminal["status"] == "CANCELLED"
    assert terminal["runtime_status"] == "FINISHED"
    assert terminal["artifact_count"] == 0


def test_waiting_approval_cancellation_revokes_gate_and_is_idempotent(
    harness: AsyncHarness,
) -> None:
    submitted = harness.client.post(
        "/v1/tasks",
        json={"task": TASK_TEXT, "require_approval": True},
    )
    task_id = submitted.json()["task_id"]
    waiting = harness.run_until(task_id, {"WAITING_APPROVAL"})
    approval_id = waiting["pending_approval_id"]
    assert isinstance(approval_id, str)

    first = harness.client.post(f"/v1/tasks/{task_id}/cancel")
    second = harness.client.post(f"/v1/tasks/{task_id}/cancel")
    assert first.status_code == second.status_code == 202
    assert first.json()["status"] == second.json()["status"] == "CANCELLED"
    approval = harness.client.get(f"/v1/tasks/{task_id}/approvals/{approval_id}")
    assert approval.status_code == 200
    assert approval.json()["status"] == "REVOKED"

    stale_resolution = harness.client.post(
        f"/v1/tasks/{task_id}/approvals/{approval_id}",
        json={"action": "approve", "reason": "This gate was already revoked."},
    )
    assert stale_resolution.status_code == 409
    terminal = harness.run_until(task_id, {"CANCELLED"})
    assert terminal["runtime_status"] == "FINISHED"
    assert terminal["artifact_count"] == 0


def test_completed_dispatch_redelivery_after_ack_loss_is_an_authoritative_noop(
    harness: AsyncHarness,
) -> None:
    """A terminal Task absorbs the same at-least-once delivery without replaying tools."""
    submitted = harness.client.post("/v1/tasks", json={"task": TASK_TEXT})
    task_id = submitted.json()["task_id"]
    completed = harness.run_until(task_id, {"COMPLETED"})
    assert completed["artifact_count"] == 1
    assert harness.api.async_runtime_repository is not None
    assert harness.worker.container.task_queue is not None
    snapshot = harness.api.async_runtime_repository.snapshot(
        task_id,
        tenant_id=harness.tenant_id,
    )
    dispatch = harness.api.async_runtime_repository.get(
        snapshot.current_dispatch_id or "",
        tenant_id=harness.tenant_id,
    ).dispatch
    calls_before = harness.worker.container.knowledge_tool.call_count

    harness.worker.container.task_queue.rearm(dispatch)
    for _attempt in range(100):
        harness.worker.runtime.run_once()
        if harness.worker.container.task_queue.depth() == 0:
            break
        sleep(0.02)
    else:
        raise AssertionError("Terminal redelivery was not acknowledged")

    terminal = harness.client.get(f"/v1/tasks/{task_id}").json()
    assert terminal["status"] == "COMPLETED"
    assert terminal["artifact_count"] == 1
    assert harness.worker.container.knowledge_tool.call_count == calls_before


def test_terminal_heartbeat_race_releases_exact_worker_lease(
    harness: AsyncHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal commit racing with heartbeat loss must still finish runtime hosting."""
    monkeypatch.setattr(LeaseHeartbeat, "authority_lost", property(lambda _self: True))
    submitted = harness.client.post("/v1/tasks", json={"task": TASK_TEXT})
    task_id = submitted.json()["task_id"]

    completed = harness.run_until(task_id, {"COMPLETED"})

    assert completed["runtime_status"] == "FINISHED"
    assert harness.api.persistence_database is not None
    with harness.api.persistence_database.session() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(WorkflowLeaseRow)
                .where(
                    WorkflowLeaseRow.tenant_id == harness.tenant_id,
                    WorkflowLeaseRow.task_id == task_id,
                )
            )
            == 0
        )
