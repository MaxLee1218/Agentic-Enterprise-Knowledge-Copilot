"""Bounded LLM replan invariants independent of business tool execution."""

from pathlib import Path

import pytest

from copilot.agent.graph import WorkflowInterrupted
from copilot.contracts import StepResult, StepResultStatus
from copilot.llm.manifest import PlannerToolManifestBuilder
from copilot.llm.mock import MockLLM
from copilot.llm.planning import LLMPlanningService
from copilot.services.llm import LLMSchemaValidationError
from copilot.services.workflows.models import SupplierQualityCommand
from copilot.services.workflows.validation import PlanValidator
from tests.workflow_helpers import build_test_container

COMMAND = SupplierQualityCommand(
    supplier_id="SUP-001",
    material_id="MAT-001",
    time_range="2026-Q1",
)


def test_replan_requires_allowlisted_reason_and_increments_version(tmp_path: Path) -> None:
    with build_test_container(
        tmp_path / "artifacts",
        interrupt_after=("validate_plan",),
    ) as container:
        with pytest.raises(WorkflowInterrupted):
            container.service.execute(COMMAND)
        state = container.engine.get_state("T-0001", "TENANT-DEMO")
        revised = state["plan"].model_copy(update={"planning_version": 2})
        provider = MockLLM(responses_by_node={"replan": [revised]})
        planner = LLMPlanningService(
            provider=provider,
            manifest_builder=PlannerToolManifestBuilder(container.registry),
            validator=PlanValidator(
                registry=container.registry,
                max_task_steps=10,
                max_planning_version=3,
            ),
        )

        outcome = planner.replan(
            contract=state["contract"],
            current_plan=state["plan"],
            step_results=(),
            evidence_ids=("E-1",),
            reason="REPAIRABLE_VERIFICATION_FAILURE",
            trace_id="TRACE-1",
            remaining_steps=6,
        )

        assert outcome.plan.planning_version == 2
        assert outcome.validation.is_valid
        assert provider.calls[0].context.prompt_version == "replan-v1"
        assert "E-1" in provider.calls[0].messages[1].content


def test_replan_rejects_security_or_permission_reason_without_model_call(
    tmp_path: Path,
) -> None:
    with build_test_container(
        tmp_path / "artifacts",
        interrupt_after=("validate_plan",),
    ) as container:
        with pytest.raises(WorkflowInterrupted):
            container.service.execute(COMMAND)
        state = container.engine.get_state("T-0001", "TENANT-DEMO")
        provider = MockLLM()
        planner = LLMPlanningService(
            provider=provider,
            manifest_builder=PlannerToolManifestBuilder(container.registry),
            validator=PlanValidator(registry=container.registry, max_task_steps=10),
        )

        with pytest.raises(ValueError, match="not recoverable"):
            planner.replan(
                contract=state["contract"],
                current_plan=state["plan"],
                step_results=(),
                evidence_ids=(),
                reason="PERMISSION_DENIED",
                trace_id="TRACE-1",
                remaining_steps=6,
            )

        assert provider.calls == []


def test_replan_cannot_change_a_successful_non_report_step(tmp_path: Path) -> None:
    with build_test_container(
        tmp_path / "artifacts",
        interrupt_after=("validate_plan",),
    ) as container:
        with pytest.raises(WorkflowInterrupted):
            container.service.execute(COMMAND)
        state = container.engine.get_state("T-0001", "TENANT-DEMO")
        first = state["plan"].steps[0]
        changed = first.model_copy(update={"tool_name": "database_query"})
        revised = state["plan"].model_copy(
            update={
                "planning_version": 2,
                "steps": (changed, *state["plan"].steps[1:]),
            }
        )
        provider = MockLLM(responses_by_node={"replan": [revised]})
        planner = LLMPlanningService(
            provider=provider,
            manifest_builder=PlannerToolManifestBuilder(container.registry),
            validator=PlanValidator(registry=container.registry, max_task_steps=10),
        )
        successful = StepResult(
            step_id=first.step_id,
            status=StepResultStatus.SUCCESS,
            output=None,
            error=None,
        )

        with pytest.raises(LLMSchemaValidationError, match="successful non-report step"):
            planner.replan(
                contract=state["contract"],
                current_plan=state["plan"],
                step_results=(successful,),
                evidence_ids=("E-1",),
                reason="REPAIRABLE_VERIFICATION_FAILURE",
                trace_id="TRACE-1",
                remaining_steps=6,
            )
