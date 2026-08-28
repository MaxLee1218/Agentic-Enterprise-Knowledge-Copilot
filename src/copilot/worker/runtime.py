"""Bounded Queue consumer, dispatcher, recovery, and graceful Worker lifecycle."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from threading import Event
from time import monotonic

from copilot.contracts.async_runtime import QueueDelivery
from copilot.persistence.postgres_queue import PostgresOutboxDispatcher, PostgresTaskQueue
from copilot.persistence.postgres_recovery import PostgresRecoveryScanner
from copilot.services.observability import NoopObservability, ObservabilityPort
from copilot.services.task_execution import TaskExecutionService
from copilot.tools.cancellation import InvocationCancellationRegistry

LOGGER = logging.getLogger(__name__)


class WorkerRuntime:
    """Run finite-concurrency delivery hosting without accepting HTTP requests."""

    def __init__(
        self,
        *,
        queue: PostgresTaskQueue,
        dispatcher: PostgresOutboxDispatcher,
        recovery: PostgresRecoveryScanner,
        execution: TaskExecutionService,
        cancellations: InvocationCancellationRegistry,
        concurrency: int,
        visibility_timeout_seconds: int,
        dispatcher_batch_size: int,
        recovery_batch_size: int,
        poll_interval_seconds: float,
        shutdown_grace_seconds: int,
        runtime_metric_counts: Callable[[], tuple[int, int]],
        required_dependencies_ready: Callable[[], bool],
        observability: ObservabilityPort | None = None,
        timer: Callable[[], float] = monotonic,
    ) -> None:
        if concurrency < 1:
            raise ValueError("Worker concurrency must be positive")
        self._queue = queue
        self._dispatcher = dispatcher
        self._recovery = recovery
        self._execution = execution
        self._cancellations = cancellations
        self._concurrency = concurrency
        self._visibility_timeout_seconds = visibility_timeout_seconds
        self._dispatcher_batch_size = dispatcher_batch_size
        self._recovery_batch_size = recovery_batch_size
        self._poll_interval_seconds = poll_interval_seconds
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._runtime_metric_counts = runtime_metric_counts
        self._required_dependencies_ready = required_dependencies_ready
        self._observability = observability or NoopObservability()
        self._timer = timer
        self._stop = Event()
        self._pool = ThreadPoolExecutor(
            max_workers=concurrency,
            thread_name_prefix="task-worker",
        )
        self._futures: set[Future[str]] = set()
        self._closed = False

    @property
    def accepting_work(self) -> bool:
        if self._stop.is_set() or self._closed or not self._queue.health():
            return False
        try:
            return self._required_dependencies_ready()
        except Exception:
            return False

    def request_stop(self) -> None:
        """Stop new claims; active work continues to a safe boundary during drain."""
        self._stop.set()

    def run(self) -> None:
        """Run until stop is requested, then perform bounded cooperative draining."""
        LOGGER.info(
            "Worker runtime started",
            extra={
                "event": "worker_started",
                "status": "READY",
                "worker_concurrency": self._concurrency,
            },
        )
        try:
            while not self._stop.is_set():
                self.run_once()
                self._stop.wait(self._poll_interval_seconds)
        finally:
            self._drain()

    def run_once(self) -> int:
        """Perform one bounded coordinator/consumer pass and return new claim count."""
        if self._closed or self._stop.is_set():
            return 0
        self._collect_finished()
        try:
            active_leases, waiting_approval = self._runtime_metric_counts()
            self._observability.set_gauge("active_execution_leases", float(active_leases))
            self._observability.set_gauge("waiting_approval_count", float(waiting_approval))
        except Exception:
            LOGGER.warning(
                "Worker runtime metrics dependency unavailable",
                extra={"event": "worker_metrics_unavailable", "status": "DEGRADED"},
            )
        try:
            recovery = self._recovery.scan_batch(limit=self._recovery_batch_size)
            if recovery.recovered:
                self._observability.increment("task_recoveries", recovery.recovered)
            if recovery.exhausted:
                self._observability.increment("recovery_failures", recovery.exhausted)
        except Exception:
            self._observability.increment("recovery_failures")
            LOGGER.exception(
                "Worker recovery scan failed; polling will retry",
                extra={"event": "runtime_recovery_failed", "status": "DEGRADED"},
            )
        try:
            self._dispatcher.dispatch_batch(limit=self._dispatcher_batch_size)
        except Exception:
            LOGGER.exception(
                "Outbox dispatch failed; durable intents remain pending",
                extra={"event": "dispatch_publish_failed", "status": "DEGRADED"},
            )
        available_slots = self._concurrency - len(self._futures)
        if available_slots <= 0:
            return 0
        try:
            self._observability.set_gauge("task_queue_depth", float(self._queue.depth()))
            self._observability.set_gauge(
                "task_queue_oldest_age_seconds",
                self._queue.oldest_age_seconds(),
            )
            deliveries = self._queue.receive(
                max_messages=available_slots,
                visibility_timeout_seconds=self._visibility_timeout_seconds,
            )
        except Exception:
            LOGGER.exception(
                "Queue receive failed; Worker remains alive for recovery",
                extra={"event": "queue_receive_failed", "status": "DEGRADED"},
            )
            return 0
        for delivery in deliveries:
            queue_wait = max(
                0.0,
                (delivery.received_at - delivery.dispatch.enqueued_at).total_seconds(),
            )
            self._observability.observe("task_queue_wait_seconds", queue_wait)
            self._futures.add(self._pool.submit(self._run_delivery, delivery))
        self._observability.set_gauge("active_workers", float(len(self._futures)))
        return len(deliveries)

    def _run_delivery(self, delivery: QueueDelivery) -> str:
        """Measure one process-local active slot without changing execution semantics."""
        started = self._timer()
        try:
            return self._execution.process(delivery)
        finally:
            self._observability.observe(
                "task_execution_seconds",
                max(0.0, self._timer() - started),
            )

    def close(self) -> None:
        """Idempotently stop and drain resources owned by this runtime."""
        if self._closed:
            return
        self.request_stop()
        self._drain()

    def _collect_finished(self) -> None:
        completed = {future for future in self._futures if future.done()}
        self._futures.difference_update(completed)
        self._observability.set_gauge("active_workers", float(len(self._futures)))
        for future in completed:
            try:
                future.result()
            except Exception:
                LOGGER.exception(
                    "Uncaught Worker delivery failure",
                    extra={"event": "worker_delivery_failed", "status": "FAILED"},
                )

    def _drain(self) -> None:
        if self._closed:
            return
        deadline = self._timer() + self._shutdown_grace_seconds
        while self._futures and self._timer() < deadline:
            remaining = max(0.0, deadline - self._timer())
            done, _pending = wait(self._futures, timeout=min(0.25, remaining))
            self._futures.difference_update(done)
            for future in done:
                try:
                    future.result()
                except Exception:
                    LOGGER.exception(
                        "Worker delivery failed during drain",
                        extra={"event": "worker_delivery_failed", "status": "FAILED"},
                    )
        if self._futures:
            self._cancellations.cancel_all(reason="Worker shutdown grace period elapsed")
            LOGGER.warning(
                "Worker drain grace elapsed; leases will fence any late commits",
                extra={
                    "event": "worker_drain_expired",
                    "status": "DRAINING",
                    "active_deliveries": len(self._futures),
                },
            )
        self._pool.shutdown(wait=not self._futures, cancel_futures=True)
        self._observability.set_gauge("active_workers", 0)
        self._closed = True
        LOGGER.info(
            "Worker runtime stopped",
            extra={"event": "worker_stopped", "status": "STOPPED"},
        )


__all__ = ["WorkerRuntime"]
