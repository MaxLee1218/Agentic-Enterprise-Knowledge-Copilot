"""Worker execution-authority context and transaction-local fencing enforcement."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from copilot.contracts.async_runtime import ExecutionLease
from copilot.contracts.errors import (
    LeaseExpiredError,
    LeaseLostError,
    StaleExecutionGenerationError,
    StaleFencingTokenError,
)
from copilot.persistence.database import PersistenceDatabase
from copilot.persistence.models import WorkflowLeaseRow, WorkflowTaskRuntimeRow
from copilot.services.execution_authority import (
    bind_execution_authority,
    current_execution_authority,
)


def assert_fenced_database(
    database: PersistenceDatabase | None,
    *,
    tenant_id: str,
    task_id: str,
) -> None:
    """Prove current authority before a non-database side effect such as file publication."""
    if database is None or current_execution_authority() is None:
        return
    with database.session() as session:
        assert_fenced_session(session, tenant_id=tenant_id, task_id=task_id)


def assert_fenced_session(session: Session, *, tenant_id: str, task_id: str) -> None:
    """Lock and verify exact generation, lease, worker, fencing token, and expiry."""
    authority = current_execution_authority()
    if authority is None:
        return
    if authority.tenant_id != tenant_id or authority.task_id != task_id:
        raise LeaseLostError("Worker commit scope does not match bound execution authority")
    runtime = session.scalar(
        select(WorkflowTaskRuntimeRow)
        .where(
            WorkflowTaskRuntimeRow.tenant_id == tenant_id,
            WorkflowTaskRuntimeRow.task_id == task_id,
        )
        .with_for_update()
    )
    if runtime is None:
        raise LeaseLostError("Worker runtime scope no longer exists")
    if runtime.execution_generation != authority.execution_generation:
        raise StaleExecutionGenerationError("Worker execution generation is stale")
    if runtime.fencing_counter != authority.fencing_token:
        raise StaleFencingTokenError("Worker fencing token is stale")
    lease = session.scalar(
        select(WorkflowLeaseRow)
        .where(
            WorkflowLeaseRow.tenant_id == tenant_id,
            WorkflowLeaseRow.task_id == task_id,
        )
        .with_for_update()
    )
    if lease is None or not _matches(lease, authority):
        raise LeaseLostError("Worker no longer owns the current execution lease")
    observed = session.scalar(select(func.now()))
    if not isinstance(observed, datetime):
        raise RuntimeError("database did not return a timestamp")
    if _as_utc(observed) >= _as_utc(lease.expires_at):
        raise LeaseExpiredError("Worker execution lease has expired")


def _matches(row: WorkflowLeaseRow, lease: ExecutionLease) -> bool:
    return (
        row.dispatch_id == lease.dispatch_id
        and row.execution_generation == lease.execution_generation
        and row.worker_id == lease.worker_id
        and row.lease_id == lease.lease_id
        and row.fencing_token == lease.fencing_token
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "assert_fenced_database",
    "assert_fenced_session",
    "bind_execution_authority",
    "current_execution_authority",
]
