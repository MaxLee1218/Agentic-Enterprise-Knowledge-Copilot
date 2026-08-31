"""Opt-in live DeepSeek stability harness for lightweight governed planning."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory

from copilot.agent.graph import WorkflowInterrupted
from copilot.agent.state import AgentGraphState
from copilot.bootstrap.container import build_workflow_container
from copilot.config import ConfigurationError, Settings
from copilot.contracts import MoneyThreshold, TaskType
from copilot.llm.deepseek import DeepSeekProvider
from copilot.llm.stability import ObservedLLMProvider, StructuredCallObservation
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TaskOutputFormat,
    TrustedCallerContext,
)

SUPPLIER_TASK = "Analyze supplier quality for Q3 2026 and generate a PDF report."
AP_TASK = (
    "Analyze Accounts Payable exceptions from 2026-04-01 to 2026-06-30 "
    "for LE-CN-01 and generate a JSON report."
)
_PLANNER_NODES = {"create_plan", "repair_plan", "replan"}
_AP_POLICY_CHECKSUM = "sha256:3095ebb099a2db12dffbc699cf1f65bb7d8e324d025eb701af4bf825d6adab33"


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    task_type: TaskType
    task: str
    output_format: TaskOutputFormat
    max_steps: int
    caller: TrustedCallerContext


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run real DeepSeek understanding and ProposedPlan compilation repeatedly; "
            "business tools never execute."
        )
    )
    parser.add_argument("--runs", type=int, default=20, help="Runs per selected scenario")
    parser.add_argument(
        "--scenario",
        choices=("supplier", "accounts-payable", "both"),
        default="supplier",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="Override the standard task for a single selected scenario",
    )
    arguments = parser.parse_args()
    if arguments.runs < 1:
        parser.error("--runs must be at least 1")
    if arguments.scenario == "both" and arguments.task is not None:
        parser.error("--task cannot be combined with --scenario both")
    return arguments


def _supplier(task: str | None = None) -> Scenario:
    task_type = TaskType.SUPPLIER_QUALITY_ANALYSIS_V1
    return Scenario(
        name="supplier",
        task_type=task_type,
        task=task or SUPPLIER_TASK,
        output_format=TaskOutputFormat.PDF,
        max_steps=10,
        caller=TrustedCallerContext(
            user_id="U-PLANNER-STABILITY-SUPPLIER",
            tenant_id="TENANT-PLANNER-STABILITY",
            data_scope=("quality.v1", "supplier-quality-policy-v1"),
            allowed_task_types=(task_type,),
            roles=("quality_analyst",),
            scopes=("task:execute", "data:quality.v1"),
            purpose=task_type.value,
            authentication_source="local_stability_harness",
            is_demo_identity=False,
        ),
    )


def _accounts_payable(task: str | None = None) -> Scenario:
    task_type = TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1
    return Scenario(
        name="accounts-payable",
        task_type=task_type,
        task=task or AP_TASK,
        output_format=TaskOutputFormat.JSON,
        max_steps=14,
        caller=TrustedCallerContext(
            user_id="U-PLANNER-STABILITY-AP",
            tenant_id="TENANT-PLANNER-STABILITY",
            data_scope=("accounts_payable.v1", "accounts-payable-policy-v1"),
            legal_entity_ids=("LE-CN-01",),
            currency_scope=("CNY",),
            allowed_task_types=(task_type,),
            roles=("finance_analyst",),
            scopes=("task:execute", "finance:ap.detail", "artifact.write"),
            purpose=task_type.value,
            policy_rule_set_id="accounts-payable-v1",
            policy_rule_set_version="ap_rules.2026.1",
            policy_manifest_checksum=_AP_POLICY_CHECKSUM,
            policy_materiality=(MoneyThreshold(currency="CNY", amount=Decimal("5000")),),
            policy_snapshot_at=datetime(2026, 7, 1, tzinfo=UTC),
            authentication_source="local_stability_harness",
            is_demo_identity=False,
        ),
    )


def _selected_scenarios(arguments: argparse.Namespace) -> tuple[Scenario, ...]:
    if arguments.scenario == "supplier":
        return (_supplier(arguments.task),)
    if arguments.scenario == "accounts-payable":
        return (_accounts_payable(arguments.task),)
    return (_supplier(), _accounts_payable())


def _planning_observations(
    observations: list[StructuredCallObservation],
    start: int,
) -> tuple[StructuredCallObservation, ...]:
    return tuple(item for item in observations[start:] if item.node_name in _PLANNER_NODES)


def _root_failure(state: AgentGraphState) -> str | None:
    errors = state.get("errors", [])
    if not errors:
        return None
    details = errors[-1].details.root
    value = details.get("planner_error_code")
    return str(value) if value is not None else errors[-1].error_code


def _run_result(
    *,
    scenario: Scenario,
    run: int,
    state: AgentGraphState,
    observations: tuple[StructuredCallObservation, ...],
) -> dict[str, object]:
    plan_valid = state.get("route") == "plan_valid"
    has_parsed = any(item.parse_status == "passed" for item in observations)
    has_proposal = any(item.schema_status == "passed" for item in observations)
    failure = _root_failure(state)
    return {
        "scenario": scenario.name,
        "run": run,
        "provider_success": bool(observations)
        and all(
            item.error_code is None
            or item.error_code in {"LLM_INVALID_RESPONSE_ERROR", "LLM_SCHEMA_VALIDATION_ERROR"}
            for item in observations
        ),
        "json_parse_success": has_parsed,
        "proposed_plan_valid": has_proposal,
        "compile_success": state.get("plan") is not None and failure is None,
        "final_plan_valid": plan_valid,
        "repair_count": state.get("plan_repair_count", 0),
        "planner_model_calls": len(observations),
        "latency_ms": sum(item.latency_ms or 0 for item in observations),
        "prompt_tokens": sum(item.input_tokens for item in observations),
        "completion_tokens": sum(item.output_tokens for item in observations),
        "raw_output_chars": sum(item.raw_output_chars for item in observations),
        "failure": failure,
    }


def _percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 2)


def _aggregate(results: list[dict[str, object]]) -> dict[str, object]:
    latencies = [_as_int(result["latency_ms"]) for result in results]
    failures = Counter(
        str(result["failure"]) for result in results if result["failure"] is not None
    )
    return {
        "runs": len(results),
        "provider_success": sum(bool(item["provider_success"]) for item in results),
        "json_parse_success": sum(bool(item["json_parse_success"]) for item in results),
        "proposed_plan_valid": sum(bool(item["proposed_plan_valid"]) for item in results),
        "compile_success": sum(bool(item["compile_success"]) for item in results),
        "final_plan_valid": sum(bool(item["final_plan_valid"]) for item in results),
        "repair_count": sum(_as_int(item["repair_count"]) for item in results),
        "latency_ms_p50": median(latencies) if latencies else None,
        "latency_ms_p95": _percentile(latencies, 0.95),
        "prompt_tokens": sum(_as_int(item["prompt_tokens"]) for item in results),
        "completion_tokens": sum(_as_int(item["completion_tokens"]) for item in results),
        "raw_output_chars": sum(_as_int(item["raw_output_chars"]) for item in results),
        "failure_breakdown": dict(sorted(failures.items())),
    }


def _as_int(value: object) -> int:
    if isinstance(value, int):
        return value
    raise TypeError("stability metric must be an integer")


def main() -> int:
    """Run the live opt-in gate, or report an explicit unverified status."""
    arguments = _arguments()
    settings = Settings(
        database_url="sqlite:///unused-planner-stability.db",
        checkpoint_enabled=False,
    )
    if settings.llm_provider != "deepseek":
        print(
            json.dumps(
                {
                    "status": "NOT_VERIFIED",
                    "reason": "set LLM_PROVIDER=deepseek and LLM_API_KEY to run live",
                },
                sort_keys=True,
            )
        )
        return 0
    try:
        api_key = settings.require_llm_api_key().get_secret_value()
    except ConfigurationError as exc:
        print(json.dumps({"status": "NOT_VERIFIED", "reason": str(exc)}, sort_keys=True))
        return 0

    deepseek = DeepSeekProvider(
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
    observed = ObservedLLMProvider(deepseek)
    results: list[dict[str, object]] = []
    try:
        with TemporaryDirectory(prefix="copilot-planner-stability-") as temporary:
            root = Path(temporary)
            run_settings = settings.model_copy(
                update={
                    "artifact_dir": root / "artifacts",
                    "checkpoint_enabled": False,
                    "database_url": f"sqlite:///{root / 'business.db'}",
                }
            )
            with build_workflow_container(
                run_settings,
                llm_provider=observed,
                interrupt_after=("validate_plan",),
            ) as container:
                for scenario in _selected_scenarios(arguments):
                    for run in range(1, arguments.runs + 1):
                        start = len(observed.observations)
                        try:
                            execution = container.task_service.submit(
                                NaturalLanguageTaskCommand(
                                    task=scenario.task,
                                    task_type=scenario.task_type,
                                    output_format=scenario.output_format,
                                    max_steps=scenario.max_steps,
                                    source=RequestSource.INTERNAL,
                                    trace_id=f"TRACE-STABILITY-{scenario.name}-{run}",
                                ),
                                scenario.caller,
                            )
                            task_id = execution.task_result.task_id
                        except WorkflowInterrupted as exc:
                            task_id = exc.task_id
                        state = container.engine.get_state(task_id, scenario.caller.tenant_id)
                        result = _run_result(
                            scenario=scenario,
                            run=run,
                            state=state,
                            observations=_planning_observations(
                                observed.observations,
                                start,
                            ),
                        )
                        results.append(result)
                        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    finally:
        deepseek.close()

    summary = {
        "status": "VERIFIED" if all(item["final_plan_valid"] for item in results) else "FAILED",
        **_aggregate(results),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["status"] == "VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
