"""Stage 8 deterministic Accounts Payable Plan profile coverage."""

from datetime import UTC, datetime
from pathlib import Path

from copilot.contracts import (
    APExceptionType,
    CapabilityName,
    ProposedPlan,
    ProposedStep,
    TaskRequest,
)
from copilot.llm.manifest import PlannerToolManifestBuilder
from copilot.llm.mock import MockLLM
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.llm.planning import LLMPlanningService
from copilot.services.workflows.accounts_payable_plan import (
    AGGREGATE_EXCEPTION_SUMMARY,
    AGGREGATE_SUPPLIER_RATE,
    AccountsPayableAnalysisPlanFactory,
    ap_report_step_id,
)
from copilot.services.workflows.validation import PlanValidator
from tests.unit.domain.ap_helpers import make_ap_contract
from tests.workflow_helpers import build_test_container


def _request() -> TaskRequest:
    return TaskRequest(
        id="R-AP-PLAN-001",
        user_id="U-FINANCE-001",
        raw_input="Analyze Q2 2026 Accounts Payable exceptions",
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
    )


def _proposal() -> ProposedPlan:
    return ProposedPlan(
        steps=(
            ProposedStep(
                step_id="knowledge",
                capability=CapabilityName.KNOWLEDGE_SEARCH,
                purpose="Retrieve AP policy",
            ),
            ProposedStep(
                step_id="database",
                capability=CapabilityName.DATABASE_QUERY,
                purpose="Retrieve AP datasets",
            ),
            ProposedStep(
                step_id="analysis",
                capability=CapabilityName.ANALYSIS_ENGINE,
                purpose="Detect and summarize AP exceptions",
                depends_on=("database",),
            ),
            ProposedStep(
                step_id="report",
                capability=CapabilityName.REPORT_GENERATOR,
                purpose="Generate the AP report",
                depends_on=("knowledge", "analysis"),
            ),
        )
    )


def test_full_ap_plan_has_14_exact_profile_bound_steps(tmp_path: Path) -> None:
    with build_test_container(tmp_path / "artifacts") as container:
        contract = make_ap_contract()
        plan = AccountsPayableAnalysisPlanFactory(container.registry).create(_request(), contract)
        validation = PlanValidator(
            registry=container.registry,
            max_task_steps=14,
        ).evaluate(plan, contract)

        assert validation.is_valid
        assert len(plan.steps) == 14
        assert plan.steps[-1].step_id.endswith("generate-accounts-payable-report")
        assert sum(step.tool_name == "database_query" for step in plan.steps) == 5
        assert sum(step.tool_name == "analysis_engine" for step in plan.steps) == 7


def test_exception_subset_selects_only_its_mapped_dataset_and_operation(
    tmp_path: Path,
) -> None:
    with build_test_container(tmp_path / "artifacts") as container:
        base = make_ap_contract()
        constraints = base.constraints.model_copy(
            update={"exception_types": (APExceptionType.EXACT_DUPLICATE_INVOICE,)}
        )
        contract = base.model_copy(update={"constraints": constraints})
        plan = AccountsPayableAnalysisPlanFactory(container.registry).create(_request(), contract)

        assert len(plan.steps) == 7
        assert {step.step_id.rsplit(":", 1)[-1] for step in plan.steps} == {
            "retrieve-ap-policy",
            "query-ap-invoice-population",
            "query-ap-duplicate-candidates",
            "analyze-ap-exact-duplicates",
            AGGREGATE_EXCEPTION_SUMMARY,
            AGGREGATE_SUPPLIER_RATE,
            "generate-accounts-payable-report",
        }


def test_ap_plan_validator_rejects_operation_id_escape(tmp_path: Path) -> None:
    with build_test_container(tmp_path / "artifacts") as container:
        contract = make_ap_contract()
        plan = AccountsPayableAnalysisPlanFactory(container.registry).create(_request(), contract)
        escaped = plan.steps[6].model_copy(
            update={"step_id": f"{contract.task_id}:run-arbitrary-analysis"}
        )
        invalid = plan.model_copy(update={"steps": (*plan.steps[:6], escaped, *plan.steps[7:])})

        result = PlanValidator(
            registry=container.registry,
            max_task_steps=14,
        ).evaluate(invalid, contract)

        assert any(issue.error_code == "AP_PLAN_OPERATION_SET_MISMATCH" for issue in result.errors)


def test_offline_planner_uses_the_same_ap_plan_rules(tmp_path: Path) -> None:
    with build_test_container(tmp_path / "artifacts") as container:
        planner = LLMPlanningService(
            provider=OfflineMockLLM(),
            manifest_builder=PlannerToolManifestBuilder(container.registry),
            validator=PlanValidator(registry=container.registry, max_task_steps=14),
        )
        outcome = planner.create_plan(
            contract=make_ap_contract(),
            trace_id="TRACE-AP-PLAN",
            max_steps=14,
        )

        assert outcome.validation.is_valid
        assert len(outcome.plan.steps) == 14


def test_wrong_ap_plan_is_repaired_to_the_exact_profile(tmp_path: Path) -> None:
    with build_test_container(tmp_path / "artifacts") as container:
        contract = make_ap_contract()
        valid = AccountsPayableAnalysisPlanFactory(container.registry).create(_request(), contract)
        invalid_report = (
            _proposal().steps[-1].model_copy(update={"capability": CapabilityName.ANALYSIS_ENGINE})
        )
        invalid = _proposal().model_copy(
            update={"steps": (*_proposal().steps[:-1], invalid_report)}
        )
        provider = MockLLM(
            responses_by_node={
                "create_plan": [invalid],
                "repair_plan": [_proposal()],
            }
        )
        planner = LLMPlanningService(
            provider=provider,
            manifest_builder=PlannerToolManifestBuilder(container.registry),
            validator=PlanValidator(registry=container.registry, max_task_steps=14),
        )

        outcome = planner.create_validated_plan(
            request=_request(),
            contract=contract,
            trace_id="TRACE-AP-REPAIR",
            max_steps=14,
        )

        assert outcome.validation.is_valid
        assert outcome.plan.steps == valid.steps
        assert outcome.repair_attempts == 1
        assert [call.context.node_name for call in provider.calls] == [
            "create_plan",
            "repair_plan",
        ]


def test_ap_replan_preserves_the_profile_and_increments_version(tmp_path: Path) -> None:
    with build_test_container(tmp_path / "artifacts") as container:
        planner = LLMPlanningService(
            provider=OfflineMockLLM(),
            manifest_builder=PlannerToolManifestBuilder(container.registry),
            validator=PlanValidator(registry=container.registry, max_task_steps=14),
        )
        contract = make_ap_contract()
        current = planner.create_plan(
            contract=contract,
            trace_id="TRACE-AP-REPLAN",
            max_steps=14,
        ).plan

        outcome = planner.replan(
            contract=contract,
            current_plan=current,
            step_results=(),
            evidence_ids=(),
            reason="RECOVERABLE_TOOL_FAILURE_EXHAUSTED",
            trace_id="TRACE-AP-REPLAN",
            remaining_steps=14,
        )

        assert outcome.validation.is_valid
        assert outcome.plan.planning_version == 2
        assert tuple(step.step_id for step in outcome.plan.steps[:-1]) == tuple(
            step.step_id for step in current.steps[:-1]
        )
        assert outcome.plan.steps[-1].step_id == ap_report_step_id(contract.task_id, 2)
        assert outcome.plan.steps[-1].step_id != current.steps[-1].step_id
