"""Versioned JSON reports, failure diagnostics, and atomic latest updates."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from evaluation.contracts import EvaluationCase, EvaluationRunResult
from evaluation.reporting.markdown_reporter import render_markdown

_SECRET_KEY = re.compile(r"(api[_-]?key|authorization|token|password|secret)", re.IGNORECASE)
_SECRET_VALUE = re.compile(r"(?i)(?:api[-_ ]?key|password|secret|bearer)[-_: =][A-Za-z0-9._-]+")


def write_reports(
    run: EvaluationRunResult,
    cases: dict[str, EvaluationCase],
    output_root: Path,
    *,
    update_latest: bool,
) -> Path:
    """Write an immutable run directory and optionally atomically refresh latest files."""
    run_directory = output_root / "runs" / run.run_id
    failure_directory = run_directory / "failures"
    failure_directory.mkdir(parents=True, exist_ok=False)
    payload = redact(run.model_dump(mode="json"))
    markdown = render_markdown(run)
    _atomic_text(run_directory / "report.json", _json(payload))
    _atomic_text(run_directory / "report.md", markdown)
    _atomic_text(
        run_directory / "manifest.json",
        _json(
            {
                "schema_version": run.schema_version,
                "run_id": run.run_id,
                "dataset_id": run.dataset_id,
                "dataset_version": run.dataset_version,
                "dataset_hash": run.dataset_hash,
                "config_hash": run.config_hash,
                "fixture_hash": run.fixture_hash,
                "seed": run.seed,
            }
        ),
    )
    for result in run.case_results:
        if result.status.value == "passed":
            continue
        case = cases[result.case_id]
        failure = {
            "case_id": result.case_id,
            "category": result.category,
            "original_task": case.task_input.raw_input,
            "actor_context_summary": {
                "user_id": case.actor_context.user_id,
                "tenant_id": case.actor_context.tenant_id,
                "role": case.actor_context.role,
            },
            "expected_outcome": case.expected_outcome.model_dump(mode="json"),
            "actual_outcome": result.status.value,
            "task_id": result.task_id,
            "trace_id": result.trace_id,
            "terminal_status": (
                result.terminal_task_status.value if result.terminal_task_status else None
            ),
            "task_contract": (
                result.task_contract.model_dump(mode="json") if result.task_contract else None
            ),
            "plan": result.plan_snapshot.model_dump(mode="json") if result.plan_snapshot else None,
            "plan_validation": [
                item.model_dump(mode="json")
                for item in result.metric_results
                if item.metric_name in {"initial_plan_validity", "final_plan_validity"}
            ],
            "plan_repairs": [
                event for event in result.workflow_events if "REPAIR" in str(event.get("event", ""))
            ],
            "tool_calls": [item.model_dump(mode="json") for item in result.tool_calls],
            "tool_results": [item.model_dump(mode="json") for item in result.tool_results],
            "retry_history": [
                event for event in result.workflow_events if "RETRY" in str(event.get("event", ""))
            ],
            "replan_history": [
                event for event in result.workflow_events if "REPLAN" in str(event.get("event", ""))
            ],
            "approval_history": [item.model_dump(mode="json") for item in result.approvals],
            "step_results": [item.model_dump(mode="json") for item in result.step_results],
            "evidence_summary": result.evidence_summary,
            "verification_result": (
                result.verification_result.model_dump(mode="json")
                if result.verification_result
                else None
            ),
            "artifact_summary": result.artifact_summary,
            "errors": [item.model_dump(mode="json") for item in result.errors],
            "warnings": result.warnings,
            "metric_results": [item.model_dump(mode="json") for item in result.metric_results],
            "primary_failure_category": result.primary_failure_category,
            "failure_categories": result.failure_categories,
            "diagnostic_message": "; ".join(result.diagnostics),
        }
        _atomic_text(failure_directory / f"{result.case_id}.json", _json(redact(failure)))
    if update_latest:
        output_root.mkdir(parents=True, exist_ok=True)
        _atomic_text(output_root / "latest.json", _json(payload))
        _atomic_text(output_root / "latest.md", markdown)
    return run_directory


def redact(value: object) -> object:
    """Recursively redact secret-shaped keys and bearer-like values."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else redact(child)
            for key, child in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(child) for child in value]
    if isinstance(value, str) and value.casefold().startswith("bearer "):
        return "[REDACTED]"
    if isinstance(value, str):
        return _SECRET_VALUE.sub("[REDACTED]", value)
    return value


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


__all__ = ["redact", "write_reports"]
