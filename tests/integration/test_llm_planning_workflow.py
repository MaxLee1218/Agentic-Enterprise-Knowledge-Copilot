"""LLM understanding, plan repair, validation, and governed execution integration."""

from pathlib import Path

import pytest

from copilot.agent.graph import WorkflowInterrupted
from copilot.contracts import (
    ArtifactType,
    ReportLanguage,
    TaskStatus,
    TaskType,
)
from copilot.llm.mock import MockLLM
from copilot.llm.schemas import (
    TaskUnderstandingOutput,
    UnderstandingConstraints,
    UnderstandingDeliverable,
    UnderstandingEntities,
    UnderstandingTimeRange,
)
from copilot.services.workflows.models import SupplierQualityCommand
from tests.workflow_helpers import build_test_container

COMMAND = SupplierQualityCommand(
    supplier_id="SUP-001",
    material_id="MAT-001",
    time_range="2026-Q1",
)


def _understanding() -> TaskUnderstandingOutput:
    return TaskUnderstandingOutput(
        goal="Analyze supplier quality and generate an evidence-backed report",
        task_type=TaskType.SUPPLIER_QUALITY_ANALYSIS_V1,
        entities=UnderstandingEntities(supplier_ids=("SUP-001",)),
        time_range=UnderstandingTimeRange(year=2026, quarter=1),
        deliverable=UnderstandingDeliverable(
            artifact_type=ArtifactType.QUALITY_ANALYSIS_REPORT_JSON,
            language=ReportLanguage.EN_US,
        ),
        constraints=UnderstandingConstraints(max_steps=10),
    )


def test_llm_plan_repair_is_checkpointed_and_invalid_plan_never_executes(
    tmp_path: Path,
) -> None:
    with build_test_container(
        tmp_path / "seed" / "artifacts",
        interrupt_after=("create_plan",),
    ) as seed:
        with pytest.raises(WorkflowInterrupted):
            seed.service.execute(COMMAND)
        seeded = seed.engine.get_state("T-0001", "TENANT-DEMO")
    valid = seeded["plan"]
    report = valid.steps[-1]
    invalid_report = report.model_copy(update={"dependency": (valid.steps[2].step_id,)})
    invalid = valid.model_copy(update={"steps": (*valid.steps[:-1], invalid_report)})
    provider = MockLLM(
        responses_by_node={
            "understand_task": [_understanding()],
            "create_plan": [invalid],
            "repair_plan": [valid],
        }
    )

    with build_test_container(
        tmp_path / "llm" / "artifacts",
        llm_provider=provider,
    ) as container:
        execution = container.service.execute(COMMAND)
        state = container.engine.get_state("T-0001", "TENANT-DEMO")
        events = [record.event for record in container.workflow_audit.list()]

    assert execution.final_state.state is TaskStatus.COMPLETED
    assert state["plan_repair_count"] == 1
    assert len(provider.calls) == 3
    assert "PLAN_VALIDATION_FAILED" in events
    assert "PLAN_REPAIR_STARTED" in events
    assert "PLAN_REPAIRED" in events
    valid_step_ids = {step.step_id for step in valid.steps}
    assert all(result.step_id in valid_step_ids for result in execution.step_results)


def test_missing_year_fails_before_planner_or_tools(tmp_path: Path) -> None:
    missing = _understanding().model_copy(
        update={
            "time_range": UnderstandingTimeRange(),
            "missing_information": ("year and quarter",),
        }
    )
    provider = MockLLM(responses_by_node={"understand_task": [missing]})

    with build_test_container(
        tmp_path / "missing" / "artifacts",
        llm_provider=provider,
    ) as container:
        execution = container.service.execute(COMMAND)

        assert execution.final_state.state is TaskStatus.FAILED
        assert container.tool_audit.list() == ()
        assert [call.context.node_name for call in provider.calls] == ["understand_task"]


def test_prompt_injection_is_data_and_cannot_expand_scope(tmp_path: Path) -> None:
    attempted = _understanding().model_copy(
        update={
            "entities": UnderstandingEntities(supplier_ids=("SUP-001", "UNAUTHORIZED-SUPPLIER"))
        }
    )
    provider = MockLLM(responses_by_node={"understand_task": [attempted]})

    with build_test_container(
        tmp_path / "scope" / "artifacts",
        llm_provider=provider,
    ) as container:
        execution = container.service.execute(COMMAND)

        assert execution.final_state.state is TaskStatus.FAILED
        assert container.tool_audit.list() == ()
        assert provider.calls[0].context.prompt_version == "task-understanding-v1"
