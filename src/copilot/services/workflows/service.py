"""Use-case service that creates frozen contracts and runs the fixed plan."""

from __future__ import annotations

import re
from calendar import monthrange
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from copilot.contracts import (
    ApprovalRequirement,
    ArtifactType,
    CapabilityName,
    ExpectedOutput,
    JsonObject,
    ReportLanguage,
    TaskConstraints,
    TaskContract,
    TaskRequest,
    TaskType,
)
from copilot.services.workflows.fixed_plan import SupplierQualityAnalysisPlanFactory
from copilot.services.workflows.models import SupplierQualityCommand, WorkflowExecution
from copilot.services.workflows.ports import IdentifierFactory
from copilot.services.workflows.runner import WorkflowRunner

_TIME_RANGE = re.compile(r"^(?P<year>\d{4})-Q(?P<quarter>[1-4])$")


class SupplierQualityWorkflowService:
    """Application entry point for the deterministic offline v1 scenario."""

    def __init__(
        self,
        *,
        runner: WorkflowRunner,
        plan_factory: SupplierQualityAnalysisPlanFactory,
        ids: IdentifierFactory,
        clock: Callable[[], datetime],
    ) -> None:
        self._runner = runner
        self._plan_factory = plan_factory
        self._ids = ids
        self._clock = clock

    def execute(self, command: SupplierQualityCommand) -> WorkflowExecution:
        """Create immutable request/contract objects and run their fixed plan."""
        year, quarter, start_date, end_date = parse_time_range(command.time_range)
        if not command.supplier_id.strip():
            raise ValueError("supplier_id must not be blank")
        if not command.material_id.strip():
            raise ValueError("material_id must not be blank")
        now = self._clock()
        task_id = self._ids.new_id("T")
        request = TaskRequest(
            id=self._ids.new_id("R"),
            user_id=command.user_id,
            raw_input=(
                f"Analyze supplier quality for {command.supplier_id}, requested material "
                f"{command.material_id}, during {command.time_range}."
            ),
            created_at=now,
            metadata=JsonObject(
                {
                    "material_id": command.material_id,
                    "note": (
                        "Material is preserved as request metadata; quality.v1 scopes by supplier."
                    ),
                }
            ),
        )
        language = ReportLanguage(command.language)
        contract = TaskContract(
            task_id=task_id,
            contract_version=1,
            task_type=TaskType.SUPPLIER_QUALITY_ANALYSIS_V1,
            required_capabilities=tuple(CapabilityName),
            expected_output=ExpectedOutput(
                artifact_type=ArtifactType.QUALITY_ANALYSIS_REPORT_JSON,
                required_sections=(
                    "scope",
                    "quality_policy_findings",
                    "supplier_quality_data",
                    "analysis_results",
                    "key_risks",
                    "recommendations",
                    "evidence_references",
                ),
                language=language,
                citations_required=True,
            ),
            constraints=TaskConstraints(
                year=year,
                quarter=quarter,
                start_date=start_date,
                end_date=end_date,
                supplier_ids=(command.supplier_id,),
                tenant_id=command.tenant_id,
                data_scope=("quality.v1", "supplier-quality-policy-v1"),
                metrics=(
                    "defect_count",
                    "inspected_count",
                    "defect_rate",
                    "period_over_period_trend",
                ),
                deadline_at=now + timedelta(minutes=5),
            ),
            approval_requirement=ApprovalRequirement(required=False),
            created_at=now,
        )
        plan = self._plan_factory.create(request, contract)
        return self._runner.run(request, contract, plan)


def parse_time_range(value: str) -> tuple[int, int, date, date]:
    """Parse the frozen explicit ``YYYY-QN`` scope into inclusive dates."""
    match = _TIME_RANGE.fullmatch(value.strip())
    if match is None:
        raise ValueError("time_range must use YYYY-Q1 through YYYY-Q4")
    year = int(match.group("year"))
    quarter = int(match.group("quarter"))
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    start = date(year, start_month, 1)
    end = date(year, end_month, monthrange(year, end_month)[1])
    return year, quarter, start, end


def utc_clock() -> datetime:
    """Default service clock kept injectable for tests."""
    return datetime.now(UTC)
