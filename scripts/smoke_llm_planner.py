"""Opt-in real DeepSeek stability test for understanding and validated planning."""

from __future__ import annotations

import argparse
import json
from typing import Any

from copilot.agent.graph import WorkflowInterrupted
from copilot.bootstrap.container import build_workflow_container
from copilot.config import ConfigurationError, Settings
from copilot.contracts import ArtifactType
from copilot.llm.deepseek import DeepSeekProvider
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TrustedCallerContext,
)

DEFAULT_TASK = "Analyze supplier quality for Q2 2026\nand generate a PDF report."
EXPECTED_TOOLS = (
    "knowledge_search",
    "database_query",
    "analysis_engine",
    "report_generator",
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Call DeepSeek repeatedly and stop after deterministic plan validation."
    )
    parser.add_argument("--runs", type=int, default=5, help="Number of independent runs")
    parser.add_argument("--task", default=DEFAULT_TASK, help="Natural-language task to validate")
    arguments = parser.parse_args()
    if arguments.runs < 1:
        parser.error("--runs must be at least 1")
    return arguments


def _summary(state: dict[str, Any], *, run: int) -> dict[str, object]:
    contract = state["contract"]
    plan = state["plan"]
    tools = tuple(step.tool_name for step in plan.steps)
    checks = {
        "year_2026": contract.constraints.year == 2026,
        "quarter_q2": contract.constraints.quarter == 2,
        "output_pdf": (
            contract.expected_output.artifact_type
            is ArtifactType.QUALITY_ANALYSIS_REPORT_PDF
        ),
        "tool_order": tools == EXPECTED_TOOLS,
        "plan_validator": state["route"] == "plan_valid",
    }
    return {
        "run": run,
        "passed": all(checks.values()),
        "checks": checks,
        "understanding": {
            "year": contract.constraints.year,
            "quarter": f"Q{contract.constraints.quarter}",
            "output": (
                "PDF"
                if contract.expected_output.artifact_type
                is ArtifactType.QUALITY_ANALYSIS_REPORT_PDF
                else contract.expected_output.artifact_type.value
            ),
        },
        "planner": list(tools),
        "planning_version": plan.planning_version,
        "plan_repair_count": state.get("plan_repair_count", 0),
        "plan_validation": "PASSED" if state["route"] == "plan_valid" else "FAILED",
    }


def main() -> int:
    """Run no business tools; stop after every real LLM plan is validated."""
    arguments = _arguments()
    settings = Settings(
        database_url="sqlite:///data/database/enterprise_demo.db",
        checkpoint_enabled=False,
    )
    if settings.llm_provider != "deepseek":
        print("SKIP: set LLM_PROVIDER=deepseek to run the real planner stability test")
        return 0
    try:
        api_key = settings.require_llm_api_key().get_secret_value()
    except ConfigurationError as exc:
        print(f"SKIP: {exc}")
        return 0
    provider = DeepSeekProvider(
        api_key=api_key,
        model=settings.llm_model,
        base_url=str(settings.llm_base_url),
        connect_timeout_seconds=settings.llm_connect_timeout_seconds,
        read_timeout_seconds=settings.llm_read_timeout_seconds,
        max_retries=settings.llm_max_retries,
        retry_base_delay_seconds=settings.llm_retry_base_delay_seconds,
        user_agent=settings.llm_user_agent,
        trace_header=settings.llm_trace_header,
    )
    caller = TrustedCallerContext(
        user_id="U-LLM-SMOKE",
        tenant_id="TENANT-LLM-SMOKE",
        data_scope=("quality.v1", "supplier-quality-policy-v1"),
        roles=("quality_analyst",),
        scopes=("task:execute", "data:quality.v1"),
    )
    results: list[dict[str, object]] = []
    try:
        with build_workflow_container(
            settings,
            llm_provider=provider,
            interrupt_after=("validate_plan",),
        ) as container:
            for run in range(1, arguments.runs + 1):
                try:
                    container.task_service.submit(
                        NaturalLanguageTaskCommand(
                            task=arguments.task,
                            source=RequestSource.INTERNAL,
                            trace_id=f"TRACE-DEEPSEEK-STABILITY-{run}",
                        ),
                        caller,
                    )
                except WorkflowInterrupted as exc:
                    task_id = exc.task_id
                else:
                    print(f"FAIL: run {run} did not stop after plan validation")
                    return 1
                state = container.engine.get_state(task_id, caller.tenant_id)
                result = _summary(state, run=run)
                results.append(result)
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    finally:
        provider.close()

    passed = sum(bool(result["passed"]) for result in results)
    final = {
        "passed_runs": passed,
        "total_runs": len(results),
        "stability_rate": passed / len(results),
        "all_passed": passed == len(results),
    }
    print(json.dumps(final, ensure_ascii=False, sort_keys=True))
    return 0 if final["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
