"""Deterministic Stage 13 task-management smoke test."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from copilot.bootstrap.container import build_workflow_container
from copilot.config import Settings
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TaskOutputFormat,
    TrustedCallerContext,
)


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Run a deterministic offline API/CLI task-management smoke scenario."
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Submit, inspect, and download one isolated offline Supplier Quality task."""
    _parser().parse_args(argv)
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
                }
                failed = [name for name, passed in checks.items() if not passed]
                if failed:
                    raise RuntimeError(f"Smoke checks failed: {', '.join(failed)}")
                print(f"Task ID: {task.task_id}")
                print(f"Trace ID: {task.trace_id}")
                print(f"Status: {task.status}")
                print(f"Steps: {len(steps)}")
                print(f"Evidence: {len(evidence)}")
                print(f"Artifact ID: {artifacts[0].artifact_id}")
                print("Stage 13 smoke: passed")
        return 0
    except Exception as exc:
        print(f"SMOKE_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
