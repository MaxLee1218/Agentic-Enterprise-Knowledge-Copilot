"""Independent PostgreSQL Queue Worker composition root."""

from __future__ import annotations

import signal
from dataclasses import dataclass
from uuid import uuid4

from copilot.bootstrap.container import WorkflowContainer, build_application
from copilot.config import Settings, get_settings
from copilot.contracts.async_runtime import (
    LeaseTimingPolicy,
    RuntimeRetryPolicy,
    WorkerIdentity,
)
from copilot.contracts.validators import utc_now
from copilot.persistence.postgres_recovery import PostgresRecoveryScanner
from copilot.services.task_execution import TaskExecutionService
from copilot.worker.runtime import WorkerRuntime


@dataclass(slots=True)
class WorkerApplication:
    """Own the shared workflow graph plus the process-specific Queue consumer."""

    container: WorkflowContainer
    runtime: WorkerRuntime

    def close(self) -> None:
        self.runtime.close()
        self.container.close()

    def __enter__(self) -> WorkerApplication:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def build_worker_application(settings: Settings) -> WorkerApplication:
    """Compose a Worker only when PostgreSQL Queue and checkpoint dependencies exist."""
    container = build_application(settings)
    database = container.persistence_database
    runtime_repository = container.async_runtime_repository
    queue = container.task_queue
    dispatcher = container.outbox_dispatcher
    cancellations = container.cancellations
    if (
        database is None
        or database.backend != "postgresql"
        or runtime_repository is None
        or queue is None
        or dispatcher is None
        or cancellations is None
    ):
        container.close()
        raise RuntimeError("Worker requires migrated PostgreSQL persistence and Queue v1")
    if not settings.checkpoint_enabled:
        container.close()
        raise RuntimeError("Worker requires durable PostgreSQL LangGraph checkpoints")
    identity = WorkerIdentity(
        worker_id=f"WORKER-{uuid4().hex}",
        deployment_id=settings.worker_deployment_id,
        started_at=utc_now(),
    )
    execution = TaskExecutionService(
        runtime=runtime_repository,
        tasks=container.repository,
        approvals=container.approval_repository,
        queue=queue,
        engine=container.engine,
        worker=identity,
        cancellations=cancellations,
        clock=utc_now,
        lease_timing=LeaseTimingPolicy(
            heartbeat_interval_seconds=settings.execution_heartbeat_interval_seconds,
            lease_ttl_seconds=settings.execution_lease_ttl_seconds,
        ),
        retry_policy=RuntimeRetryPolicy(
            max_recovery_attempts=settings.max_runtime_recovery_attempts,
            initial_backoff_seconds=5,
            maximum_backoff_seconds=20,
            backoff_multiplier=2,
        ),
    )
    recovery = PostgresRecoveryScanner(
        database,
        max_recovery_attempts=settings.max_runtime_recovery_attempts,
    )
    worker = WorkerRuntime(
        queue=queue,
        dispatcher=dispatcher,
        recovery=recovery,
        execution=execution,
        cancellations=cancellations,
        concurrency=settings.worker_concurrency,
        visibility_timeout_seconds=settings.task_queue_visibility_timeout_seconds,
        dispatcher_batch_size=settings.dispatcher_batch_size,
        recovery_batch_size=settings.recovery_batch_size,
        poll_interval_seconds=settings.worker_poll_interval_seconds,
        shutdown_grace_seconds=settings.worker_shutdown_grace_seconds,
        runtime_metric_counts=runtime_repository.runtime_metric_counts,
        required_dependencies_ready=lambda: (
            container.readiness is not None and container.readiness.check().accepts_tasks
        ),
        observability=container.observability,
    )
    return WorkerApplication(container=container, runtime=worker)


def main() -> None:
    """Run one Worker process with graceful SIGTERM/SIGINT claim shutdown."""
    settings = get_settings()
    with build_worker_application(settings) as application:

        def stop(_signum: int, _frame: object) -> None:
            application.runtime.request_stop()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        application.runtime.run()


__all__ = ["WorkerApplication", "build_worker_application", "main"]
