"""Deterministic Stage 16 task, observability, and performance smoke test."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from copilot.bootstrap.container import build_workflow_container
from copilot.config import Settings
from copilot.contracts import SpanKind, TraceSpan
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TaskOutputFormat,
    TrustedCallerContext,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a deterministic offline API/CLI task-management smoke scenario."
    )
    parser.add_argument(
        "--show-trace",
        action="store_true",
        help="Print the complete sanitized Trace as JSON in addition to the readable span list.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Submit, inspect, and download one isolated offline Supplier Quality task."""
    args = _parser().parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix="copilot-stage13-") as temporary:
            root = Path(temporary)
            settings = Settings(
                app_env="test",
                database_url="sqlite:///unused-stage13-smoke.db",
                artifact_dir=root / "artifacts",
                checkpoint_database_path=root / "workflow.db",
                checkpoint_enabled=True,
                llm_provider="mock",
            )
            caller = TrustedCallerContext(
                user_id="U-SMOKE",
                tenant_id="TENANT-SMOKE",
                data_scope=("quality.v1", "supplier-quality-policy-v1"),
                roles=("quality_data_approver",),
            )
            with build_workflow_container(
                settings,
                llm_provider=OfflineMockLLM(),
                sleeper=lambda _seconds: None,
            ) as container:
                execution = container.task_service.submit(
                    NaturalLanguageTaskCommand(
                        task=(
                            "Analyze Q2 2026 supplier quality deviations and generate "
                            "a JSON management report."
                        ),
                        output_format=TaskOutputFormat.JSON,
                        source=RequestSource.CLI,
                    ),
                    caller,
                )
                task_id = execution.task_result.task_id
                task = container.task_service.get_task(task_id, caller)
                steps = container.task_service.list_task_steps(task_id, caller)
                evidence = container.task_service.list_task_evidence(task_id, caller)
                artifacts = container.artifact_service.list_task_artifacts(task_id, caller)
                if not artifacts:
                    raise RuntimeError("Smoke task did not produce Artifact metadata")
                download = container.artifact_service.get_task_artifact(
                    task_id,
                    artifacts[0].artifact_id,
                    caller,
                )
                report = json.loads(download.path.read_text(encoding="utf-8"))
                task_summary = report.get("task_summary", {})
                telemetry = container.observability
                if telemetry is None:
                    raise RuntimeError("Observability runtime was not composed")
                trace = telemetry.spans_for_trace(task.trace_id)
                trace_summary = telemetry.trace_summary(task.trace_id, status=task.status)
                performance = telemetry.analyze_trace(task.trace_id, status=task.status)
                metrics = telemetry.metrics_snapshot()
                if trace_summary is None or performance is None:
                    raise RuntimeError("Smoke task did not produce a complete Trace summary")
                span_kinds = {span.kind for span in trace}
                checks = {
                    "task_id": bool(task.task_id),
                    "trace_id": bool(task.trace_id),
                    "completed": task.status == "COMPLETED",
                    "steps": bool(steps),
                    "evidence": bool(evidence),
                    "artifact_metadata": bool(artifacts),
                    "artifact_readable": download.path.is_file(),
                    "report_task_id": task_summary.get("task_id") == task_id,
                    "report_trace_field": bool(task_summary.get("trace_id")),
                    "task_span": SpanKind.TASK in span_kinds,
                    "node_spans": SpanKind.GRAPH_NODE in span_kinds,
                    "step_spans": SpanKind.STEP in span_kinds,
                    "tool_spans": SpanKind.TOOL in span_kinds,
                }
                failed = [name for name, passed in checks.items() if not passed]
                if failed:
                    raise RuntimeError(f"Smoke checks failed: {', '.join(failed)}")
                print(f"Task ID: {task.task_id}")
                print(f"Trace ID: {task.trace_id}")
                print(f"Status: {task.status}")
                print(f"Total latency: {trace_summary.total_latency_ms:.3f} ms")
                print(f"Steps: {len(steps)}")
                print(f"Evidence: {len(evidence)}")
                print(f"Artifact ID: {artifacts[0].artifact_id}")
                print(f"Node latency: {trace_summary.stage_latencies}")
                print(f"Step latency: {_latencies(trace, SpanKind.STEP, use_step_id=True)}")
                print(f"Tool latency: {trace_summary.tool_latencies}")
                print(f"Slowest stage: {performance.slowest_stage or 'none'}")
                print(f"Slowest step: {performance.slowest_step_id or 'none'}")
                print(f"Slowest tool: {performance.slowest_tool or 'none'}")
                print(f"Retry count: {trace_summary.retry_count}")
                print(f"Replan count: {trace_summary.replan_count}")
                print(
                    "Tool failure rate: "
                    f"{metrics.failure_rates.get('tool_attempt_failure_rate', 0.0):.3f}"
                )
                task_quantiles = metrics.quantiles.get("task_latency_ms", {})
                print(f"Metrics p50: {task_quantiles.get('p50')} ms")
                print(f"Metrics p95: {task_quantiles.get('p95')} ms")
                print("Sanitized Trace:")
                for span in trace:
                    print(
                        "  "
                        f"{span.kind.value} {span.name} status={span.status.value} "
                        f"latency_ms={span.latency_ms} parent={span.parent_span_id or 'root'} "
                        f"step_id={span.step_id or 'none'}"
                    )
                if args.show_trace:
                    print(
                        json.dumps(
                            {
                                "summary": trace_summary.model_dump(mode="json"),
                                "performance": performance.model_dump(mode="json"),
                                "metrics": metrics.model_dump(mode="json"),
                                "spans": [span.model_dump(mode="json") for span in trace],
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    )
                print("Stage 13 smoke: passed")
                print("Stage 16 smoke: passed")
        return 0
    except Exception as exc:
        print(f"SMOKE_FAILED: {exc}", file=sys.stderr)
        return 1


def _latencies(
    spans: tuple[TraceSpan, ...],
    kind: SpanKind,
    *,
    use_step_id: bool = False,
) -> dict[str, float]:
    values: dict[str, float] = {}
    for span in spans:
        if span.kind is not kind:
            continue
        name = span.step_id if use_step_id else span.name
        if name is None:
            continue
        values[name] = values.get(name, 0.0) + (span.latency_ms or 0.0)
    return values


if __name__ == "__main__":
    raise SystemExit(main())
