"""Inspect one persisted task through the shared application services."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime

from copilot.bootstrap.cli import build_demo_caller
from copilot.bootstrap.container import build_application
from copilot.config import ConfigurationError, get_settings
from copilot.services.task_service import TaskPermissionDeniedError, TaskServiceError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect one governed task by ID.")
    parser.add_argument("task_id", help="Persisted task identifier.")
    parser.add_argument("--json", action="store_true", help="Emit stable JSON output.")
    return parser


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def main(argv: Sequence[str] | None = None) -> int:
    """Inspect a task without reading SQLite or Checkpoint files directly."""
    args = _parser().parse_args(argv)
    try:
        settings = get_settings()
        caller = build_demo_caller()
        with build_application(settings) as container:
            task = container.task_service.get_task(args.task_id, caller)
            steps = container.task_service.list_task_steps(args.task_id, caller)
            evidence = container.task_service.list_task_evidence(args.task_id, caller)
            artifacts = container.artifact_service.list_task_artifacts(args.task_id, caller)
    except ConfigurationError as exc:
        print(f"CONFIGURATION_ERROR: {exc}", file=sys.stderr)
        return 3
    except TaskPermissionDeniedError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 5
    except TaskServiceError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(
            json.dumps(
                {
                    "task": asdict(task),
                    "steps": [asdict(step) for step in steps],
                    "evidence": [asdict(item) for item in evidence],
                    "artifacts": [asdict(item) for item in artifacts],
                },
                default=_json_default,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    print(f"Task ID: {task.task_id}")
    print(f"Trace ID: {task.trace_id}")
    print(f"Status: {task.status}")
    print(f"Task Type: {task.task_type or 'unavailable'}")
    print(f"Current Step: {task.current_step or 'none'}")
    print(f"Started: {task.started_at.isoformat() if task.started_at else 'not started'}")
    print(f"Completed: {task.completed_at.isoformat() if task.completed_at else 'not completed'}")
    print(f"Pending Approval: {task.pending_approval_id or 'none'}")
    print(f"Error: {task.error_summary or 'none'}")
    print(f"Steps ({len(steps)}):")
    for step in steps:
        print(f"  {step.step_id} {step.tool_name} {step.status} attempts={step.attempt_count}")
    print(f"Evidence ({len(evidence)}):")
    for item in evidence:
        print(f"  {item.evidence_id} {item.type} step={item.step_id}")
    print(f"Artifacts ({len(artifacts)}):")
    for artifact in artifacts:
        print(f"  {artifact.artifact_id} {artifact.filename} {artifact.size_bytes} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
