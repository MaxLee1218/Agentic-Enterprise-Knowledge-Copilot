"""Worker lifecycle, bounded consumption, outage retry, and local metrics coverage."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from threading import Event
from typing import cast

from copilot.contracts.async_runtime import QueueDelivery, TaskDispatch
from copilot.persistence.postgres_queue import PostgresOutboxDispatcher, PostgresTaskQueue
from copilot.persistence.postgres_recovery import PostgresRecoveryScanner, RecoveryScanResult
from copilot.services.observability import NoopObservability
from copilot.services.task_execution import TaskExecutionService
from copilot.tools.cancellation import InvocationCancellationRegistry
from copilot.worker.runtime import WorkerRuntime


class _RecordingObservability(NoopObservability):
    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.gauges: dict[str, float] = {}
        self.histograms: dict[str, list[float]] = {}

    def increment(
        self,
        name: str,
        amount: int = 1,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        del labels
        self.counters[name] = self.counters.get(name, 0) + amount

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        del labels
        self.gauges[name] = value

    def observe(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        del labels
        self.histograms.setdefault(name, []).append(value)


class _Queue:
    def __init__(self, deliveries: list[QueueDelivery] | None = None) -> None:
        self.deliveries = deliveries or []
        self.fail_receive = False

    def health(self) -> bool:
        return True

    def depth(self) -> int:
        return len(self.deliveries)

    def oldest_age_seconds(self) -> float:
        return 2.5 if self.deliveries else 0.0

    def receive(
        self,
        *,
        max_messages: int,
        visibility_timeout_seconds: int,
    ) -> tuple[QueueDelivery, ...]:
        del visibility_timeout_seconds
        if self.fail_receive:
            self.fail_receive = False
            raise ConnectionError("controlled Queue outage")
        batch = self.deliveries[:max_messages]
        del self.deliveries[:max_messages]
        return tuple(batch)


class _Recovery:
    def __init__(self) -> None:
        self.fail = False

    def scan_batch(self, *, limit: int) -> RecoveryScanResult:
        del limit
        if self.fail:
            self.fail = False
            raise ConnectionError("controlled recovery outage")
        return RecoveryScanResult(examined=0, recovered=0, exhausted=0, skipped=0)


class _Dispatcher:
    def __init__(self) -> None:
        self.fail = False

    def dispatch_batch(self, *, limit: int) -> int:
        del limit
        if self.fail:
            self.fail = False
            raise ConnectionError("controlled dispatcher outage")
        return 0


class _Execution:
    def __init__(self) -> None:
        self.completed = Event()

    def process(self, _delivery: QueueDelivery) -> str:
        self.completed.set()
        return "SUCCEEDED"


def test_dependency_outage_does_not_terminate_worker_polling() -> None:
    queue = _Queue()
    queue.fail_receive = True
    recovery = _Recovery()
    recovery.fail = True
    dispatcher = _Dispatcher()
    dispatcher.fail = True
    telemetry = _RecordingObservability()
    runtime = _runtime(queue, recovery, dispatcher, _Execution(), telemetry)

    assert runtime.run_once() == 0
    assert runtime.accepting_work is True
    assert runtime.run_once() == 0
    assert telemetry.counters["recovery_failures"] == 1
    runtime.close()


def test_bounded_delivery_records_queue_and_execution_metrics() -> None:
    now = datetime.now(UTC)
    dispatch = TaskDispatch(
        tenant_id="TENANT-WORKER-UNIT",
        task_id="T-WORKER-UNIT",
        trace_id="TRACE-WORKER-UNIT",
        dispatch_id="D-WORKER-UNIT",
        execution_generation=1,
        expected_task_version=1,
        enqueued_at=now,
        not_before=now,
    )
    queue = _Queue(
        [
            QueueDelivery(
                delivery_id="QD-WORKER-UNIT",
                dispatch=dispatch,
                received_at=now,
                delivery_attempt=1,
            )
        ]
    )
    execution = _Execution()
    telemetry = _RecordingObservability()
    runtime = _runtime(queue, _Recovery(), _Dispatcher(), execution, telemetry)

    assert runtime.run_once() == 1
    assert execution.completed.wait(timeout=1)
    assert runtime.run_once() == 0
    assert telemetry.gauges["task_queue_depth"] == 0
    assert telemetry.gauges["active_workers"] == 0
    assert telemetry.histograms["task_queue_wait_seconds"] == [0.0]
    assert len(telemetry.histograms["task_execution_seconds"]) == 1
    runtime.close()


def _runtime(
    queue: _Queue,
    recovery: _Recovery,
    dispatcher: _Dispatcher,
    execution: _Execution,
    telemetry: _RecordingObservability,
) -> WorkerRuntime:
    return WorkerRuntime(
        queue=cast(PostgresTaskQueue, queue),
        dispatcher=cast(PostgresOutboxDispatcher, dispatcher),
        recovery=cast(PostgresRecoveryScanner, recovery),
        execution=cast(TaskExecutionService, execution),
        cancellations=InvocationCancellationRegistry(),
        concurrency=1,
        visibility_timeout_seconds=60,
        dispatcher_batch_size=10,
        recovery_batch_size=10,
        poll_interval_seconds=0.01,
        shutdown_grace_seconds=1,
        runtime_metric_counts=lambda: (0, 0),
        required_dependencies_ready=lambda: True,
        observability=telemetry,
    )
