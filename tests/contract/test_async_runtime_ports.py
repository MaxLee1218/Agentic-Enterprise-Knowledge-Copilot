"""Architecture contract for broker-neutral runtime ports and minimal dispatch messages."""

from __future__ import annotations

import inspect

from copilot.services.async_runtime import (
    DispatchRepository,
    LeaseRepository,
    RecoveryRepository,
    RuntimeRepository,
    TaskQueue,
    TaskSubmissionRepository,
)


def test_runtime_ports_freeze_required_capabilities_without_provider_types() -> None:
    expected = {
        TaskQueue: {"enqueue", "receive", "ack", "nack"},
        TaskSubmissionRepository: {"persist_task_and_dispatch"},
        DispatchRepository: {"create", "get", "compare_and_set_status"},
        LeaseRepository: {"try_acquire_lease", "heartbeat", "release"},
        RuntimeRepository: {"snapshot", "request_cancellation", "record_recovery_decision"},
        RecoveryRepository: {"list_recovery_candidates"},
    }

    for port, methods in expected.items():
        assert methods.issubset(port.__dict__)
        source = inspect.getsource(port).casefold()
        assert all(provider not in source for provider in ("celery", "redis", "kafka", "sqs"))
