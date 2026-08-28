"""Tenant-scoped, read-only asynchronous runtime inspection command."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from copilot.bootstrap.cli import build_demo_caller
from copilot.bootstrap.container import build_application
from copilot.config import ConfigurationError, get_settings
from copilot.services.task_service import TaskPermissionDeniedError, TaskServiceError


def main(argv: Sequence[str] | None = None) -> int:
    """Inspect durable runtime facts without exposing business payloads or credentials."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id", help="Persisted Task identifier.")
    args = parser.parse_args(argv)
    try:
        caller = build_demo_caller()
        with build_application(get_settings()) as container:
            task = container.task_service.get_task(args.task_id, caller)
            repository = container.async_runtime_repository
            if repository is None:
                raise ConfigurationError("Runtime inspection requires authoritative persistence")
            snapshot = repository.snapshot(args.task_id, tenant_id=caller.tenant_id)
            dispatch = (
                repository.get(snapshot.current_dispatch_id, tenant_id=caller.tenant_id)
                if snapshot.current_dispatch_id is not None
                else None
            )
            checkpoint = container.engine.checkpoint_identity(args.task_id, caller.tenant_id)
    except ConfigurationError as exc:
        print(f"CONFIGURATION_ERROR: {exc}", file=sys.stderr)
        return 3
    except TaskPermissionDeniedError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 5
    except TaskServiceError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "task_id": task.task_id,
                "task_status": task.status,
                "runtime_status": snapshot.runtime_status.value,
                "task_version": snapshot.task_version,
                "execution_generation": snapshot.execution_generation,
                "dispatch": dispatch.model_dump(mode="json") if dispatch is not None else None,
                "recovery_attempt_count": snapshot.recovery_attempt_count,
                "retry_not_before": (
                    snapshot.retry_not_before.isoformat()
                    if snapshot.retry_not_before is not None
                    else None
                ),
                "last_runtime_error": snapshot.last_recovery_error,
                "lease": snapshot.lease.model_dump(mode="json") if snapshot.lease else None,
                "checkpoint": (
                    checkpoint.model_dump(mode="json") if checkpoint is not None else None
                ),
                "cancellation": (
                    snapshot.cancellation.model_dump(mode="json")
                    if snapshot.cancellation is not None
                    else None
                ),
                "approval": {
                    "approval_id": snapshot.pending_approval_id,
                    "status": (
                        snapshot.pending_approval_status.value
                        if snapshot.pending_approval_status is not None
                        else None
                    ),
                },
                "successful_step_ids": list(snapshot.successful_step_ids),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


__all__ = ["main"]
