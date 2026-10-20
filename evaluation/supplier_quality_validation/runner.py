"""One captured governed Task, checked against an isolated literal validation oracle."""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Literal, Protocol

from pydantic import computed_field, model_validator

from copilot.contracts import TaskStatus
from copilot.llm.prompts import PLANNER_PROMPT_VERSION, TASK_UNDERSTANDING_PROMPT_VERSION
from copilot.services.task_intake import NaturalLanguageTaskCommand, RequestSource, TaskOutputFormat
from evaluation.contracts import EvaluationModel
from evaluation.dataset_loader import canonical_hash
from evaluation.supplier_quality_pilot.budget import PaidAttempt
from evaluation.supplier_quality_pilot.checker import evaluate_case
from evaluation.supplier_quality_pilot.contracts import ClarificationObservation, PilotAssertion
from evaluation.supplier_quality_pilot.execution import capture_task
from evaluation.supplier_quality_pilot.interfaces import ReportScope, SourceFacts
from evaluation.supplier_quality_pilot.live import LiveModelSession
from evaluation.supplier_quality_pilot.policy import PolicySnapshot
from evaluation.supplier_quality_validation.bundle import CaseOracle

LOGGER = logging.getLogger(__name__)


class CaseInput(ReportScope, Protocol):
    """Expected scope stays in the evaluator; capture_task receives only raw input."""

    @property
    def case_id(self) -> str: ...

    @property
    def request(self) -> str: ...

    @property
    def scripted_answer(self) -> str | None: ...


class ValidationRun(EvaluationModel):
    """First outcome, including failed/interrupted/unrun cases and every billed attempt."""

    version: Literal["supplier-quality-validation-run.v1"] = "supplier-quality-validation-run.v1"
    case_id: str
    expected_outcome: Literal["report", "denial"]
    mode: Literal["control", "live"]
    case_status: Literal["executed", "unrun", "interrupted"]
    run_id: str | None = None
    started_at: datetime | None = None
    latency_ms: int | None = None
    task_id: str | None = None
    task_status: TaskStatus | None = None
    error_code: str | None = None
    errors: tuple[tuple[str, str, bool], ...] = ()
    interaction: ClarificationObservation | None = None
    executed_tools: tuple[str, ...] = ()
    artifact_paths: tuple[str, ...] = ()
    artifact_checksums: dict[str, str] = {}
    assertions: tuple[PilotAssertion, ...]
    numeric_ids: tuple[str, ...]
    fact_ids: tuple[str, ...]
    attempts: tuple[PaidAttempt, ...] = ()
    source_hash: str
    policy_hash: str
    prompt_versions: tuple[str, ...] = (TASK_UNDERSTANDING_PROMPT_VERSION, PLANNER_PROMPT_VERSION)

    @model_validator(mode="after")
    def validate_required_assertions(self) -> "ValidationRun":
        """Missing/duplicate required assertions can never inflate qualified delivery."""
        if (
            tuple(a.assertion_id for a in self.assertions if a.category == "numeric")
            != self.numeric_ids
        ):
            raise ValueError("numeric assertion identities missing, duplicated or changed")
        facts = tuple(
            a.assertion_id
            for a in self.assertions
            if a.category == "evidence" and a.assertion_id in self.fact_ids
        )
        if facts != self.fact_ids:
            raise ValueError("fact assertion identities missing, duplicated or changed")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def qualified_delivery(self) -> bool:
        return self.expected_outcome == "report" and self._accepted()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def safety_passed(self) -> bool:
        return self.expected_outcome == "denial" and self._accepted()

    def _accepted(self) -> bool:
        return (
            self.case_status == "executed"
            and bool(self.assertions)
            and all(a.passed for a in self.assertions)
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def numeric_total(self) -> int:
        return len(self.numeric_ids)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def numeric_passed(self) -> int:
        return self._passed(self.numeric_ids, "numeric")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fact_total(self) -> int:
        return len(self.fact_ids)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fact_passed(self) -> int:
        return self._passed(self.fact_ids, "evidence")

    def _passed(self, keys: tuple[str, ...], category: str) -> int:
        return sum(
            any(
                a.assertion_id == key and a.category == category and a.passed
                for a in self.assertions
            )
            for key in keys
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def usd_cost(self) -> Decimal | None:
        if self.mode != "live" or any(a.status != "reconciled" for a in self.attempts):
            return None
        return sum((a.usd_cost for a in self.attempts if a.usd_cost is not None), Decimal(0))


def run_validation_case(
    output_directory: Path,
    *,
    source: SourceFacts,
    case: CaseInput,
    oracle: CaseOracle,
    policy: PolicySnapshot,
    persistence_url: str | None = None,
    live: LiveModelSession | None = None,
) -> ValidationRun:
    """Execute once through the shared capture seam, then apply the literal oracle."""
    if oracle.case_id != case.case_id:
        raise ValueError("case/oracle identity mismatch")
    started_at, timer = datetime.now(UTC), perf_counter()
    captured = capture_task(
        output_directory,
        snapshot=source,
        command=NaturalLanguageTaskCommand(
            task=case.request,
            output_format=TaskOutputFormat(case.output_format),
            source=RequestSource.INTERNAL,
        ),
        scripted_answer=case.scripted_answer,
        policy=policy,
        persistence_url=persistence_url,
        live=live,
    )
    checks = evaluate_case(
        source,
        captured.execution,
        captured.artifact_paths,
        case,
        expected_metrics=oracle.metrics,
        quarterly={
            q.supplier_id: (q.defect_count, q.inspected_count, q.defect_rate)
            for q in oracle.quarterly
        },
        policy=policy,
    )
    if captured.harness_error:
        checks += (
            PilotAssertion(assertion_id="capture_complete", category="lifecycle", passed=False),
        )
    if case.scripted_answer:
        interaction = captured.interaction
        checks += tuple(
            PilotAssertion(assertion_id=name, category="lifecycle", passed=passed)
            for name, passed in (
                (
                    "clarification_before_tools",
                    interaction is not None
                    and interaction.initial_status is TaskStatus.WAITING_CLARIFICATION
                    and interaction.tools_before_answer == 0,
                ),
                (
                    "same_task_scripted_resume",
                    interaction is not None
                    and interaction.answer_applied
                    and interaction.initial_task_id == interaction.final_task_id,
                ),
            )
        )
    if (
        tuple(a.assertion_id for a in checks if a.category == "numeric") != oracle.numeric_ids
        or tuple(a.assertion_id for a in checks if a.assertion_id in oracle.fact_ids)
        != oracle.fact_ids
    ):
        raise ValueError("checker changed frozen assertion identities")
    run = ValidationRun(
        case_id=case.case_id,
        expected_outcome=case.expected_outcome,
        mode="live" if live else "control",
        case_status="interrupted" if captured.harness_error else "executed",
        run_id=live.run_id if live else None,
        started_at=started_at,
        latency_ms=max(0, round((perf_counter() - timer) * 1000)),
        task_id=captured.task_id,
        task_status=captured.task_status,
        error_code=captured.error_code,
        interaction=captured.interaction,
        errors=tuple(
            (e.error_code, e.error_type.value, e.recoverable) for e in captured.execution.errors
        )
        if captured.execution
        else (),
        executed_tools=tuple(s.tool_name for s in captured.execution.step_executions if s.executed)
        if captured.execution
        else (),
        artifact_paths=tuple(str(p) for p in captured.artifact_paths.values()),
        artifact_checksums={a.artifact_id: a.checksum for a in captured.execution.artifacts}
        if captured.execution
        else {},
        assertions=checks,
        numeric_ids=oracle.numeric_ids,
        fact_ids=oracle.fact_ids,
        attempts=tuple(a for a in live.ledger.snapshot().attempts if a.run_id == live.run_id)
        if live
        else (),
        source_hash=canonical_hash(
            {
                "tenant_id": source.tenant_id,
                "rows": [r.model_dump(mode="json") for r in source.rows],
            }
        ),
        policy_hash=canonical_hash(policy.model_dump(mode="json")),
    )
    (output_directory / "report.json").write_text(run.model_dump_json(indent=2))
    LOGGER.info(
        "validation.case.finished",
        extra={
            "case_id": case.case_id,
            "task_id": run.task_id,
            "run_id": run.run_id,
            "qualified_delivery": run.qualified_delivery,
            "error_code": run.error_code,
            "latency_ms": run.latency_ms,
        },
    )
    return run
