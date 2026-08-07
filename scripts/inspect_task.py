"""Inspect one persisted task through the shared application services."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime

from copilot.bootstrap.cli import build_demo_caller
from copilot.bootstrap.container import WorkflowContainer, build_application
from copilot.config import ConfigurationError, get_settings
from copilot.services.task_service import TaskPermissionDeniedError, TaskServiceError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect one governed task by ID.")
    parser.add_argument("task_id", help="Persisted task identifier.")
    parser.add_argument("--json", action="store_true", help="Emit stable JSON output.")
    parser.add_argument(
        "--performance",
        action="store_true",
        help="Include persisted node/tool latency and retry analysis.",
    )
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
            performance = _performance_view(container, task.trace_id, args.task_id, task.status)
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
                    "performance": performance if args.performance else None,
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
    if args.performance:
        print("Performance:")
        print(json.dumps(performance, ensure_ascii=False, sort_keys=True, default=_json_default))
    return 0


def _performance_view(
    container: WorkflowContainer,
    trace_id: str,
    task_id: str,
    status: str,
) -> dict[str, object]:
    """Build an inspectable view from live spans or durable audit timing facts."""
    workflow_audit = container.workflow_audit
    tool_audit = container.tool_audit
    telemetry = container.observability
    workflow_events = [item for item in workflow_audit.list() if item.task_id == task_id]
    node_latency: dict[str, int] = {}
    for item in workflow_events:
        if item.event != "node_completed" or item.duration_ms is None:
            continue
        raw_name = item.metadata.root.get("node_name", item.event)
        name = raw_name if isinstance(raw_name, str) else item.event
        node_latency[name] = node_latency.get(name, 0) + item.duration_ms
    tools = [item for item in tool_audit.list() if item.task_id == task_id]
    tool_latency = {f"{item.tool_name}#attempt-{item.attempt}": item.latency_ms for item in tools}
    retries = sum(max(item.attempt - 1, 0) for item in tools)
    failures = sorted({item.error_code for item in tools if item.error_code is not None})
    replans = sum(item.event == "REPLAN_STARTED" for item in workflow_events)
    live_summary = telemetry.trace_summary(trace_id, status=status) if telemetry else None
    live_analysis = telemetry.analyze_trace(trace_id, status=status) if telemetry else None
    slowest = max(tool_latency, key=lambda name: tool_latency[name]) if tool_latency else None
    slowest_node = max(node_latency, key=lambda name: node_latency[name]) if node_latency else None
    return {
        "trace_summary": live_summary.model_dump(mode="json") if live_summary else None,
        "live_performance": live_analysis.model_dump(mode="json") if live_analysis else None,
        "node_latency_ms": node_latency,
        "tool_latency_ms": tool_latency,
        "retry_count": retries,
        "replan_count": replans,
        "failure_types": failures,
        "slowest_persisted_node": slowest_node,
        "slowest_persisted_tool_attempt": slowest,
        "note": (
            "Full spans are process-local; durable audit timings remain available after restart."
            if live_summary is None
            else "Full in-process trace is available."
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
