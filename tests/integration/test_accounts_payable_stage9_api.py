"""Stage 9 public Task API and finance authorization integration coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from copilot.api.app import create_app
from copilot.api.dependencies import get_caller_context
from copilot.bootstrap.container import WorkflowContainer, build_workflow_container
from copilot.config import Settings
from copilot.contracts import MoneyThreshold, TaskType
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.persistence.identifiers import SequentialIdentifierFactory
from copilot.security.identity import DemoIdentityProvider
from copilot.services.task_intake import TrustedCallerContext
from copilot.tools.database.ap_seed import seed_accounts_payable_demo_database
from tests.async_runtime_helpers import execute_accepted_task
from tests.workflow_helpers import fixed_clock

pytestmark = pytest.mark.integration

_TASK_TEXT = (
    "Analyze all Accounts Payable exceptions from 2026-04-01 to 2026-06-30 "
    "for LE-CN-01 and LE-US-01"
)
_MANIFEST_CHECKSUM = "sha256:3095ebb099a2db12dffbc699cf1f65bb7d8e324d025eb701af4bf825d6adab33"


def _owner() -> TrustedCallerContext:
    return TrustedCallerContext(
        user_id="U-FINANCE-OWNER",
        tenant_id="TENANT-DEMO",
        data_scope=(
            "quality.v1",
            "supplier-quality-policy-v1",
            "accounts_payable.v1",
            "accounts-payable-policy-v1",
        ),
        legal_entity_ids=("LE-CN-01", "LE-US-01"),
        currency_scope=("CNY", "USD"),
        allowed_task_types=(
            TaskType.SUPPLIER_QUALITY_ANALYSIS_V1,
            TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1,
        ),
        roles=("quality_analyst", "finance_analyst"),
        scopes=(
            "task:execute",
            "task:read",
            "evidence:read",
            "finance:ap.detail",
            "finance:ap.artifact:download",
            "artifact.write",
        ),
        purpose=TaskType.SUPPLIER_QUALITY_ANALYSIS_V1.value,
        is_demo_identity=False,
        policy_rule_set_id="accounts-payable-v1",
        policy_rule_set_version="ap_rules.2026.1",
        policy_manifest_checksum=_MANIFEST_CHECKSUM,
        policy_materiality=(
            MoneyThreshold(currency="CNY", amount=Decimal("5000")),
            MoneyThreshold(currency="USD", amount=Decimal("1000")),
        ),
        policy_snapshot_at=datetime(2026, 7, 1, tzinfo=UTC),
    )


def _client(tmp_path: Path) -> tuple[TestClient, WorkflowContainer]:
    database_url = f"sqlite:///{tmp_path / 'stage9-ap-business.db'}"
    seed_accounts_payable_demo_database(database_url)
    settings = Settings(
        app_env="test",
        database_url=database_url,
        database_provider="mock",
        persistence_database_url=f"sqlite:///{tmp_path / 'stage9-runtime.db'}",
        artifact_dir=tmp_path / "artifacts",
        checkpoint_enabled=False,
        llm_provider="mock",
        max_task_steps=14,
        max_database_rows=50_000,
        log_level="ERROR",
        observability_enabled=False,
        metrics_enabled=False,
        trace_enabled=False,
    )
    container = build_workflow_container(
        settings,
        ids=SequentialIdentifierFactory(),
        clock=fixed_clock,
        sleeper=lambda _seconds: None,
        llm_provider=OfflineMockLLM(),
    )
    app = create_app(
        task_service=container.task_service,
        task_submission_service=container.task_submission_service,
        artifact_service=container.artifact_service,
        approval_service=container.approval_service,
        settings=settings,
        observability=container.observability,
        identity_provider=DemoIdentityProvider(settings),
    )
    return TestClient(app), container


def test_public_selector_runs_ap_and_exposes_existing_task_resources(tmp_path: Path) -> None:
    client, container = _client(tmp_path)
    owner = _owner()
    app = cast(FastAPI, client.app)
    app.dependency_overrides[get_caller_context] = lambda: owner
    try:
        with client:
            supplier_only = owner.model_copy(
                update={
                    "allowed_task_types": (TaskType.SUPPLIER_QUALITY_ANALYSIS_V1,),
                }
            )
            app.dependency_overrides[get_caller_context] = lambda: supplier_only
            denied = client.post(
                "/v1/tasks",
                json={"task": _TASK_TEXT, "task_type": "accounts_payable_analysis.v1"},
            )
            app.dependency_overrides[get_caller_context] = lambda: owner
            created = client.post(
                "/v1/tasks",
                json={
                    "task": _TASK_TEXT,
                    "task_type": "accounts_payable_analysis.v1",
                    "output_format": "json",
                },
            )
            assert created.status_code == 202, created.text
            task_id = created.json()["task_id"]
            execute_accepted_task(container, task_id, tenant_id="TENANT-DEMO")
            task = client.get(f"/v1/tasks/{task_id}")
            history = client.get("/v1/tasks")
            steps = client.get(f"/v1/tasks/{task_id}/steps")
            evidence = client.get(f"/v1/tasks/{task_id}/evidence")
            artifacts = client.get(f"/v1/tasks/{task_id}/artifacts")
            artifact_id = artifacts.json()["artifacts"][0]["artifact_id"]
            download = client.get(f"/v1/tasks/{task_id}/artifacts/{artifact_id}")

        assert denied.status_code == 403
        assert task.status_code == history.status_code == 200
        assert task.json()["task_type"] == "accounts_payable_analysis.v1"
        assert history.json()["items"][0]["task_type"] == "accounts_payable_analysis.v1"
        assert len(steps.json()["steps"]) == 14
        assert (
            "supplier-quality"
            not in " ".join(item["purpose"] for item in steps.json()["steps"]).lower()
        )
        assert {item["type"] for item in evidence.json()["evidence"]} == {
            "DOCUMENT",
            "DATABASE",
            "CALCULATION",
        }
        assert artifacts.status_code == 200
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/json"
    finally:
        container.close()


def test_finance_assignment_and_download_scope_fail_closed(tmp_path: Path) -> None:
    client, container = _client(tmp_path)
    owner = _owner()
    app = cast(FastAPI, client.app)
    try:
        with client:
            app.dependency_overrides[get_caller_context] = lambda: owner
            created = client.post(
                "/v1/tasks",
                json={
                    "task": _TASK_TEXT,
                    "task_type": "accounts_payable_analysis.v1",
                    "output_format": "json",
                },
            )
            task_id = created.json()["task_id"]
            execute_accepted_task(container, task_id, tenant_id="TENANT-DEMO")
            artifact_id = client.get(f"/v1/tasks/{task_id}/artifacts").json()["artifacts"][0][
                "artifact_id"
            ]
            auditor = owner.model_copy(
                update={
                    "user_id": "U-FINANCE-AUDITOR",
                    "roles": ("finance_auditor",),
                    "purpose": TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1.value,
                    "allowed_task_types": (TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1,),
                    "assigned_task_ids": (task_id,),
                    "scopes": ("finance:ap.aggregate",),
                }
            )
            app.dependency_overrides[get_caller_context] = lambda: auditor
            history = client.get("/v1/tasks")
            detail = client.get(f"/v1/tasks/{task_id}")
            evidence = client.get(f"/v1/tasks/{task_id}/evidence")
            metadata = client.get(f"/v1/tasks/{task_id}/artifacts")
            download = client.get(f"/v1/tasks/{task_id}/artifacts/{artifact_id}")
            submit = client.post(
                "/v1/tasks",
                json={"task": _TASK_TEXT, "task_type": "accounts_payable_analysis.v1"},
            )

        assert history.status_code == detail.status_code == evidence.status_code == 200
        assert history.json()["items"][0]["task_id"] == task_id
        assert metadata.status_code == 200
        assert download.status_code == 404
        assert submit.status_code == 403
    finally:
        container.close()
