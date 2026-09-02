"""Hermetic real API composition used by browser end-to-end tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from os import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from fastapi import FastAPI

from copilot.api.app import create_app
from copilot.bootstrap.container import WorkflowContainer, build_workflow_container
from copilot.config import PROJECT_ROOT, Settings
from copilot.contracts import ClarificationResponse, TaskClarification, TaskType
from copilot.contracts.async_runtime import (
    DispatchStatus,
    LeaseTimingPolicy,
    QueueDelivery,
    RuntimeRetryPolicy,
    TaskDispatch,
    TaskSubmissionResponse,
    WorkerIdentity,
)
from copilot.contracts.validators import utc_now
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.persistence.identifiers import SequentialIdentifierFactory
from copilot.security.identity import DemoIdentityProvider
from copilot.services.clarification_service import (
    ClarificationService,
    ClarificationSubmissionResult,
)
from copilot.services.identity import IdentityRequest
from copilot.services.task_execution import TaskExecutionService
from copilot.services.task_intake import NaturalLanguageTaskCommand, TrustedCallerContext
from copilot.services.task_submission import TaskSubmissionService
from copilot.tools.database.ap_seed import seed_accounts_payable_demo_database
from copilot.tools.database.connection import DatabaseConnection
from copilot.tools.database.models import Invoice, Payment, PurchaseOrder
from copilot.tools.database.normalization import normalize_invoice_number

_runtime_directory = TemporaryDirectory(prefix="copilot-frontend-e2e-")
_runtime_path = Path(_runtime_directory.name)
_business_database_url = f"sqlite:///{_runtime_path / 'business.db'}"
_initial_working_directory = Path.cwd()
try:
    chdir(PROJECT_ROOT)
    seed_accounts_payable_demo_database(_business_database_url)
finally:
    chdir(_initial_working_directory)


def _add_august_browser_fixture(database_url: str) -> None:
    """Add one disposable paid invoice without changing the frozen committed AP seed."""
    connection = DatabaseConnection(database_url, read_only=False)
    created_at = datetime(2026, 9, 1, tzinfo=UTC)
    invoice_date = date(2026, 8, 10)
    due_date = invoice_date + timedelta(days=30)
    try:
        with connection.session() as session:
            session.add(
                PurchaseOrder(
                    id=19999,
                    tenant_id="TENANT-DEMO",
                    source_system="E2E-FIXTURE",
                    source_record_id="PO-E2E-AUG-001",
                    po_number="PO-E2E-AUG-001",
                    supplier_id=1,
                    legal_entity_id=1001,
                    business_unit_id=1101,
                    order_date=date(2026, 7, 15),
                    approved_amount=Decimal("1300.0000"),
                    currency="CNY",
                    matching_basis="SINGLE_INVOICE",
                    status="APPROVED",
                    approved_at=datetime(2026, 7, 16, tzinfo=UTC),
                    created_at=created_at,
                )
            )
            session.flush()
            session.add(
                Invoice(
                    id=29999,
                    tenant_id="TENANT-DEMO",
                    source_system="E2E-FIXTURE",
                    source_record_id="INV-E2E-AUG-001",
                    supplier_id=1,
                    legal_entity_id=1001,
                    business_unit_id=1101,
                    invoice_number="E2E-AUG-001",
                    normalized_invoice_number=normalize_invoice_number("E2E-AUG-001"),
                    invoice_type="STANDARD",
                    invoice_date=invoice_date,
                    posting_date=invoice_date + timedelta(days=1),
                    currency="CNY",
                    net_amount=Decimal("1170.0000"),
                    tax_amount=Decimal("130.0000"),
                    gross_amount=Decimal("1300.0000"),
                    purchase_order_id=19999,
                    payment_terms_days=30,
                    due_date=due_date,
                    no_po_exception_ref=None,
                    no_po_exception_approved=False,
                    status="PAID",
                    created_at=created_at,
                )
            )
            session.flush()
            session.add(
                Payment(
                    id=49999,
                    tenant_id="TENANT-DEMO",
                    source_system="E2E-FIXTURE",
                    source_record_id="PAY-E2E-AUG-001",
                    invoice_id=29999,
                    legal_entity_id=1001,
                    business_unit_id=1101,
                    payment_date=due_date,
                    payment_amount=Decimal("1300.0000"),
                    currency="CNY",
                    status="SETTLED",
                    created_at=created_at,
                )
            )
    finally:
        connection.dispose()


_add_august_browser_fixture(_business_database_url)

settings = Settings(
    app_env="test",
    database_url=_business_database_url,
    persistence_database_url=f"sqlite:///{_runtime_path / 'runtime.db'}",
    artifact_dir=_runtime_path / "artifacts",
    checkpoint_database_path=_runtime_path / "workflow.db",
    demo_approval_roles=("quality_analyst", "quality_data_approver"),
    demo_identity_profile="local_enterprise",
    execution_heartbeat_interval_seconds=1,
    execution_lease_ttl_seconds=5,
    log_level="WARNING",
    log_format="text",
)


class _E2EIdentityProvider:
    """Expose the exact authorized choices required by the browser acceptance scenario."""

    def __init__(self, configured: Settings) -> None:
        self._delegate = DemoIdentityProvider(configured)

    def resolve(self, request: IdentityRequest) -> TrustedCallerContext:
        caller = self._delegate.resolve(request)
        return caller.model_copy(
            update={
                "legal_entity_ids": ("LE-CN-01", "LE-DE-01"),
                "purpose": TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1.value,
                "policy_snapshot_at": datetime(2026, 8, 31, tzinfo=UTC),
            }
        )


class _DeterministicQueue:
    """Minimal transport receipt sink for the hermetic browser Worker driver."""

    def enqueue(self, dispatch: TaskDispatch) -> None:
        del dispatch

    def receive(
        self,
        *,
        max_messages: int,
        visibility_timeout_seconds: int,
    ) -> tuple[QueueDelivery, ...]:
        del max_messages, visibility_timeout_seconds
        return ()

    def ack(self, delivery: QueueDelivery) -> None:
        del delivery

    def nack(
        self,
        delivery: QueueDelivery,
        *,
        retry_at: datetime | None,
        reason_code: str,
    ) -> None:
        raise RuntimeError(
            f"Hermetic browser delivery was nacked: {delivery.delivery_id} {reason_code} {retry_at}"
        )

    def health(self) -> bool:
        return True

    def shutdown(self) -> None:
        return None


def _deliver_current_dispatch(
    container: WorkflowContainer,
    execution: TaskExecutionService,
    task_id: str,
    tenant_id: str,
) -> None:
    runtime = container.async_runtime_repository
    assert runtime is not None
    snapshot = runtime.snapshot(task_id, tenant_id=tenant_id)
    assert snapshot.current_dispatch_id is not None
    record = runtime.get(snapshot.current_dispatch_id, tenant_id=tenant_id)
    if record.status is DispatchStatus.PENDING:
        record = runtime.compare_and_set_status(
            record.dispatch.dispatch_id,
            tenant_id=tenant_id,
            expected=DispatchStatus.PENDING,
            replacement=DispatchStatus.ENQUEUED,
            observed_at=utc_now(),
        )
    outcome = execution.process(
        QueueDelivery(
            delivery_id=f"DELIVERY-{uuid4().hex}",
            dispatch=record.dispatch,
            received_at=utc_now(),
            delivery_attempt=1,
        )
    )
    if outcome not in {"SUSPENDED", "SUCCEEDED", "NO_OP_TERMINAL"}:
        raise RuntimeError(f"Hermetic browser Worker returned {outcome}")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Own one deterministic offline workflow runtime for the browser suite."""
    try:
        with build_workflow_container(
            settings,
            ids=SequentialIdentifierFactory(),
            sleeper=lambda _seconds: None,
            llm_provider=OfflineMockLLM(),
        ) as container:
            assert container.task_submission_service is not None
            assert container.async_runtime_repository is not None
            assert container.cancellations is not None
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="frontend-e2e-worker")
            queue = _DeterministicQueue()
            execution = TaskExecutionService(
                runtime=container.async_runtime_repository,
                tasks=container.repository,
                approvals=container.approval_repository,
                clarifications=container.clarification_repository,
                queue=queue,
                engine=container.engine,
                worker=WorkerIdentity(
                    worker_id="WORKER-FRONTEND-E2E",
                    deployment_id="frontend-e2e",
                    started_at=utc_now(),
                ),
                cancellations=container.cancellations,
                clock=utc_now,
                lease_timing=LeaseTimingPolicy(
                    heartbeat_interval_seconds=1,
                    lease_ttl_seconds=5,
                ),
                retry_policy=RuntimeRetryPolicy(
                    max_recovery_attempts=1,
                    initial_backoff_seconds=1,
                    maximum_backoff_seconds=1,
                    backoff_multiplier=1,
                ),
            )

            class _SubmissionDriver:
                """Return 202 acceptance, then drive the hermetic Task off-request."""

                def __init__(self, delegate: TaskSubmissionService) -> None:
                    self._delegate = delegate

                def submit(
                    self,
                    command: NaturalLanguageTaskCommand,
                    caller: TrustedCallerContext,
                    *,
                    idempotency_key: str | None,
                ) -> TaskSubmissionResponse:
                    accepted = self._delegate.submit(
                        command,
                        caller,
                        idempotency_key=idempotency_key,
                    )
                    executor.submit(
                        _deliver_current_dispatch,
                        container,
                        execution,
                        accepted.task_id,
                        caller.tenant_id,
                    )
                    return accepted

            class _ClarificationDriver:
                """Return 202, then deliver the durable resume dispatch off-request."""

                def __init__(self, delegate: ClarificationService) -> None:
                    self._delegate = delegate

                def get(
                    self,
                    task_id: str,
                    clarification_id: str,
                    caller: TrustedCallerContext,
                    *,
                    trace_id: str = "",
                ) -> TaskClarification:
                    return self._delegate.get(
                        task_id,
                        clarification_id,
                        caller,
                        trace_id=trace_id,
                    )

                def respond(
                    self,
                    task_id: str,
                    clarification_id: str,
                    response: ClarificationResponse,
                    caller: TrustedCallerContext,
                    *,
                    trace_id: str = "",
                ) -> ClarificationSubmissionResult:
                    accepted = self._delegate.respond(
                        task_id,
                        clarification_id,
                        response,
                        caller,
                        trace_id=trace_id,
                    )
                    if not accepted.reused:
                        executor.submit(
                            _deliver_current_dispatch,
                            container,
                            execution,
                            accepted.clarification.task_id,
                            caller.tenant_id,
                        )
                    return accepted

            application.state.task_service = container.task_service
            application.state.e2e_container = container
            application.state.task_submission_service = _SubmissionDriver(
                container.task_submission_service
            )
            application.state.approval_service = container.approval_service
            application.state.clarification_service = _ClarificationDriver(
                container.clarification_service
            )
            application.state.artifact_service = container.artifact_service
            application.state.observability = container.observability
            application.state.readiness = container.readiness
            try:
                yield
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
    finally:
        _runtime_directory.cleanup()


app = create_app(
    settings=settings,
    identity_provider=_E2EIdentityProvider(settings),
    lifespan=lifespan,
)

__all__ = ["app"]
