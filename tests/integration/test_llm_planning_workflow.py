"""LLM understanding, plan repair, validation, and governed execution integration."""

from pathlib import Path

import pytest

from copilot.agent.graph import WorkflowInterrupted
from copilot.contracts import (
    ArtifactType,
    CapabilityName,
    ProposedPlan,
    ProposedStep,
    ReportLanguage,
    TaskConstraints,
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
from copilot.services.workflows.planning import TaskUnderstandingOutcome
from copilot.tools.analytics.schemas import AnalyticsResult
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
        include_policy_comparison=True,
    )


def _proposal() -> ProposedPlan:
    return ProposedPlan(
        steps=(
            ProposedStep(
                step_id="knowledge",
                capability=CapabilityName.KNOWLEDGE_SEARCH,
                purpose="Retrieve supplier quality policy",
            ),
            ProposedStep(
                step_id="database",
                capability=CapabilityName.DATABASE_QUERY,
                purpose="Retrieve supplier quality data",
            ),
            ProposedStep(
                step_id="analysis",
                capability=CapabilityName.ANALYSIS_ENGINE,
                purpose="Analyze supplier quality metrics",
                depends_on=("database",),
            ),
            ProposedStep(
                step_id="report",
                capability=CapabilityName.REPORT_GENERATOR,
                purpose="Generate the requested report",
                depends_on=("knowledge", "analysis"),
            ),
        )
    )


@pytest.mark.parametrize(
    "supplier,separator,note",
    [
        ("SUP-001", " in ", ""),
        ("SUP-2027", " in ", ""),
        ("SUP-2027", " ", ""),
        ("SUP-001", " in ", " Show defect count 2000."),
    ],
)
def test_a_partial_time_candidate_pauses_for_the_missing_year(
    tmp_path: Path, supplier: str, separator: str, note: str
) -> None:
    from copilot.llm.schemas import ClarificationAssistantOutput
    from copilot.services.task_intake import (
        NaturalLanguageTaskCommand,
        RequestSource,
        TrustedCallerContext,
    )

    candidate = _understanding().model_dump(mode="json")
    candidate["time_range"] = {"year": None, "quarter": 3}
    candidate["entities"] = {"supplier_ids": [supplier]}
    provider = MockLLM(
        responses_by_node={
            "understand_task": [candidate],
            "compose_clarification": [
                ClarificationAssistantOutput(
                    message="Which reporting year should be used?", covered_fields=("time_range",)
                )
            ],
        }
    )
    raw = f"Analyze supplier quality for {supplier}{separator}Q3 and generate JSON.{note}"
    caller = TrustedCallerContext(
        user_id="U-QUALITY",
        tenant_id="TENANT-DEMO",
        data_scope=("quality.v1",),
        supplier_ids=(supplier, "SUP-002"),
    )
    with build_test_container(tmp_path / "partial", llm_provider=provider) as container:
        with pytest.raises(WorkflowInterrupted) as caught:
            container.task_service.submit(
                NaturalLanguageTaskCommand(task=raw, source=RequestSource.API), caller
            )
        assert caught.value.status == "WAITING_CLARIFICATION"
        state = container.engine.get_state(caught.value.task_id, caller.tenant_id)
        assert state["contract"] is None and state["plan"] is None
        assert state["tool_results"] == []
        assert state["request"].raw_input == raw


def test_llm_plan_repair_is_checkpointed_and_invalid_plan_never_executes(
    tmp_path: Path,
) -> None:
    with build_test_container(
        tmp_path / "seed" / "artifacts",
        interrupt_after=("create_plan",),
    ) as seed:
        with pytest.raises(WorkflowInterrupted):
            seed.service.execute(COMMAND)
        assert seed.engine.get_state("T-0001", "TENANT-DEMO")["plan"] is not None
    invalid_report = (
        _proposal().steps[-1].model_copy(update={"capability": CapabilityName.ANALYSIS_ENGINE})
    )
    invalid = _proposal().model_copy(update={"steps": (*_proposal().steps[:-1], invalid_report)})
    provider = MockLLM(
        responses_by_node={
            "understand_task": [_understanding()],
            "create_plan": [invalid],
            "repair_plan": [_proposal()],
        }
    )

    with build_test_container(
        tmp_path / "llm" / "artifacts",
        llm_provider=provider,
    ) as container:
        execution = container.service.execute(COMMAND)
        state = container.engine.get_state("T-0001", "TENANT-DEMO")
        events = [record.event for record in container.workflow_audit.list(tenant_id="TENANT-DEMO")]

    assert execution.final_state.state is TaskStatus.COMPLETED
    assert state["plan_repair_count"] == 1
    assert len(provider.calls) == 3
    assert "PLAN_REPAIRED" in events
    assert [result.step_id for result in execution.step_results] == [
        "T-0001:knowledge",
        "T-0001:database",
        "T-0001:analysis",
        "T-0001:report",
    ]


def test_explicit_period_is_not_erased_by_model_missing_candidate(tmp_path: Path) -> None:
    missing = _understanding().model_copy(
        update={
            "time_range": UnderstandingTimeRange(),
            "missing_information": ("year and quarter",),
        }
    )
    provider = MockLLM(
        responses_by_node={"understand_task": [missing], "create_plan": [_proposal()]}
    )

    with build_test_container(
        tmp_path / "missing" / "artifacts",
        llm_provider=provider,
    ) as container:
        execution = container.service.execute(COMMAND)

        assert execution.final_state.state is TaskStatus.COMPLETED
        assert [call.context.node_name for call in provider.calls] == [
            "understand_task",
            "create_plan",
        ]


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
        assert container.tool_audit.list(tenant_id="TENANT-DEMO") == ()
        assert provider.calls[0].context.prompt_version == "task-understanding-v4"


def test_partial_model_metrics_cannot_remove_required_report_trends(tmp_path: Path) -> None:
    candidate = _understanding().model_copy(
        update={
            "constraints": UnderstandingConstraints(
                max_steps=10, metrics=("defect_count", "inspected_count", "defect_rate")
            )
        }
    )
    provider = MockLLM(
        responses_by_node={"understand_task": [candidate], "create_plan": [_proposal()]}
    )
    with build_test_container(tmp_path / "metrics", llm_provider=provider) as container:
        execution = container.service.execute(COMMAND)
        state = container.engine.get_state(execution.task_result.task_id, "TENANT-DEMO")
        assert execution.task_result.final_status is TaskStatus.COMPLETED
        constraints = state["contract"].constraints
        assert isinstance(constraints, TaskConstraints)
        assert constraints.metrics == (
            "defect_count",
            "inspected_count",
            "defect_rate",
            "period_over_period_trend",
        )
        analysis = next(r for r in execution.step_results if r.step_id.endswith(":analysis"))
        assert analysis.output is not None
        result = AnalyticsResult.model_validate(analysis.output.root)
        assert any(m.metric.value == "period_over_period_trend" for m in result.metrics)


@pytest.mark.parametrize("answer", ["Q2 2026", "Q2 2026 for SUP-002"])
def test_time_clarification_keeps_original_supplier_boundary(tmp_path: Path, answer: str) -> None:
    from copilot.contracts import ClarificationResponse
    from copilot.llm.schemas import ClarificationAssistantOutput
    from copilot.services.task_intake import (
        NaturalLanguageTaskCommand,
        RequestSource,
        TaskOutputFormat,
        TrustedCallerContext,
    )
    from copilot.services.workflows.errors import TaskUnderstandingAuthorizationError

    candidate = _understanding().model_copy(
        update={
            "entities": UnderstandingEntities(supplier_ids=("SUP-001", "SUP-002")),
            "time_range": UnderstandingTimeRange(),
        }
    )
    provider = MockLLM(
        responses_by_node={
            "understand_task": [candidate, candidate],
            "compose_clarification": [
                ClarificationAssistantOutput(
                    message="Which year and quarter?", covered_fields=("time_range",)
                )
            ],
        }
    )
    with build_test_container(tmp_path / "scope-resume", llm_provider=provider) as container:
        request, context = container.task_service.prepare(
            NaturalLanguageTaskCommand(
                task="Analyze SUP-001 supplier quality and generate JSON.",
                source=RequestSource.INTERNAL,
                output_format=TaskOutputFormat.JSON,
            ),
            TrustedCallerContext(
                user_id="U-QUALITY",
                tenant_id="TENANT-DEMO",
                supplier_ids=("SUP-001", "SUP-002"),
                data_scope=("quality.v1",),
            ),
        )
        assert container.planning_service is not None
        first = container.planning_service.understand(
            request=request, trusted_context=context, trace_id=context.trace_id, max_steps=14
        )
        assert first.contract is None
        assert first.clarification_context.values.root["supplier_ids"] == ["SUP-001"]
        planning = container.planning_service

        def resume() -> TaskUnderstandingOutcome:
            return planning.understand(
                request=request,
                trusted_context=context,
                trace_id=context.trace_id,
                max_steps=14,
                clarification_context=first.clarification_context,
                clarification_response=ClarificationResponse(message=answer),
            )

        if "SUP-002" in answer:
            with pytest.raises(TaskUnderstandingAuthorizationError):
                resume()
        else:
            final = resume()
            assert final.contract is not None
            assert final.contract.constraints.supplier_ids == ("SUP-001",)
            assert request.raw_input == "Analyze SUP-001 supplier quality and generate JSON."


def test_unknown_model_metric_is_rejected_before_tool_execution(tmp_path: Path) -> None:
    candidate = _understanding().model_copy(
        update={"constraints": UnderstandingConstraints(max_steps=10, metrics=("profit",))}
    )
    provider = MockLLM(responses_by_node={"understand_task": [candidate]})
    with build_test_container(tmp_path / "unsupported-metric", llm_provider=provider) as container:
        execution = container.service.execute(COMMAND)
        assert execution.task_result.final_status is TaskStatus.FAILED
        assert not any(record.executed for record in execution.step_executions)
        assert not execution.evidence
