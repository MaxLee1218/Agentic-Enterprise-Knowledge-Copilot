"""Reusable Stage B contract suite for a real PostgreSQL runtime repository.

This module is intentionally not named ``test_*.py``: Stage A has no runtime migration or
repository implementation to execute. A Stage B PostgreSQL test must subclass
``AsyncRuntimePostgresLeaseContract``, provide the harness fixture, and thereby collect these
tests unchanged. Two harness repositories must own independent database connections.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from typing import Protocol

import pytest

from copilot.contracts.async_runtime import (
    ExecutionLease,
    LeaseAcquisitionResult,
    LeaseAcquisitionStatus,
    LeaseTimingPolicy,
    TaskDispatch,
    WorkerIdentity,
)
from copilot.contracts.errors import (
    LeaseExpiredError,
    LeaseLostError,
    StaleFencingTokenError,
)
from copilot.services.async_runtime import LeaseRepository


class PostgresRuntimeContractHarness(Protocol):
    """Stage B adapter seam; its clock must advance database-observed time without sleeping."""

    repositories: tuple[LeaseRepository, LeaseRepository]
    dispatch: TaskDispatch
    workers: tuple[WorkerIdentity, WorkerIdentity]
    timing: LeaseTimingPolicy
    initial_time: datetime

    def advance_database_time(self, observed_at: datetime) -> None:
        """Move the injected/test database clock to an exact UTC instant."""
        ...

    def attempt_fenced_probe_commit(
        self,
        lease: ExecutionLease,
    ) -> None:
        """Attempt a harmless authoritative CAS guarded by this lease's fencing identity."""
        ...


class AsyncRuntimePostgresLeaseContract:
    """Executable concurrency/fencing tests inherited by the Stage B PostgreSQL adapter."""

    @pytest.fixture
    def runtime_harness(self) -> PostgresRuntimeContractHarness:
        """Return a migrated disposable database harness with two independent connections."""
        raise NotImplementedError("Stage B PostgreSQL adapter must provide runtime_harness")

    def test_simultaneous_acquire_has_exactly_one_winner(
        self,
        runtime_harness: PostgresRuntimeContractHarness,
    ) -> None:
        barrier = Barrier(2)

        def acquire(index: int) -> LeaseAcquisitionResult:
            barrier.wait(timeout=5)
            return runtime_harness.repositories[index].try_acquire_lease(
                runtime_harness.dispatch,
                runtime_harness.workers[index],
                timing=runtime_harness.timing,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(acquire, (0, 1)))

        assert sum(result.status is LeaseAcquisitionStatus.ACQUIRED for result in results) == 1
        assert sum(result.status is LeaseAcquisitionStatus.CONFLICT for result in results) == 1
        winning_lease = next(result.lease for result in results if result.lease is not None)
        assert winning_lease.fencing_token >= 1

    def test_takeover_increments_fencing_and_rejects_old_worker(
        self,
        runtime_harness: PostgresRuntimeContractHarness,
    ) -> None:
        first = runtime_harness.repositories[0].try_acquire_lease(
            runtime_harness.dispatch,
            runtime_harness.workers[0],
            timing=runtime_harness.timing,
        )
        assert first.status is LeaseAcquisitionStatus.ACQUIRED
        assert first.lease is not None

        takeover_time = runtime_harness.initial_time + timedelta(
            seconds=runtime_harness.timing.lease_ttl_seconds
        )
        runtime_harness.advance_database_time(takeover_time)
        second = runtime_harness.repositories[1].try_acquire_lease(
            runtime_harness.dispatch,
            runtime_harness.workers[1],
            timing=runtime_harness.timing,
        )

        assert second.status is LeaseAcquisitionStatus.ACQUIRED
        assert second.lease is not None
        assert second.lease.execution_generation == first.lease.execution_generation
        assert second.lease.fencing_token > first.lease.fencing_token

        with pytest.raises((LeaseExpiredError, LeaseLostError, StaleFencingTokenError)):
            runtime_harness.repositories[0].heartbeat(
                first.lease,
                timing=runtime_harness.timing,
            )
        assert runtime_harness.repositories[0].release(first.lease) is False
        with pytest.raises((LeaseExpiredError, LeaseLostError, StaleFencingTokenError)):
            runtime_harness.attempt_fenced_probe_commit(first.lease)


__all__ = ["AsyncRuntimePostgresLeaseContract", "PostgresRuntimeContractHarness"]
