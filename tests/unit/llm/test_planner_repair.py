"""Layered ProposedPlan recovery and typed planner-failure coverage."""

from pathlib import Path
from typing import Any

import pytest

from copilot.bootstrap.container import WorkflowContainer
from copilot.contracts import CapabilityName, ProposedPlan, ProposedStep
from copilot.llm.manifest import PlannerToolManifestBuilder
from copilot.llm.mock import MockLLM
from copilot.llm.planning import LLMPlanningService
from copilot.services.llm import LLMTimeoutError, LLMUnavailableError
from copilot.services.workflows.errors import (
    PlannerInvalidJsonError,
    PlannerProviderError,
    PlannerRepairExhaustedError,
    PlannerSchemaValidationError,
    PlannerTimeoutError,
)
from copilot.services.workflows.planning import PlanGenerationOutcome
from copilot.services.workflows.validation import PlanValidator
from tests.unit.domain.helpers import make_contract
from tests.workflow_helpers import build_test_container


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
                purpose="Retrieve governed supplier quality data",
            ),
            ProposedStep(
                step_id="analysis",
                capability=CapabilityName.ANALYSIS_ENGINE,
                purpose="Calculate approved metrics",
                depends_on=("database",),
            ),
            ProposedStep(
                step_id="report",
                capability=CapabilityName.REPORT_GENERATOR,
                purpose="Generate the management report",
                depends_on=("knowledge", "analysis"),
            ),
        )
    )


def _planner(
    container: WorkflowContainer,
    provider: MockLLM,
    *,
    structured_retries: int = 1,
    targeted_repairs: int = 2,
) -> LLMPlanningService:
    registry = container.registry
    return LLMPlanningService(
        provider=provider,
        manifest_builder=PlannerToolManifestBuilder(registry),
        validator=PlanValidator(registry=registry, max_task_steps=10),
        max_structured_output_retries=structured_retries,
        max_plan_repair_attempts=targeted_repairs,
    )


def _create(planner: LLMPlanningService) -> PlanGenerationOutcome:
    return planner.create_plan(
        contract=make_contract(),
        trace_id="TRACE-REPAIR",
        max_steps=10,
    )


def test_invalid_json_gets_one_bounded_structured_output_retry(tmp_path: Path) -> None:
    provider = MockLLM(responses_by_node={"create_plan": ['{"steps":', _proposal()]})
    with build_test_container(tmp_path / "artifacts") as container:
        outcome = _create(_planner(container, provider))

    assert outcome.validation.is_valid
    assert outcome.structured_output_retries == 1
    assert outcome.repair_attempts == 0
    assert len(outcome.model_calls) == 2
    assert outcome.model_calls[0].parse_status == "failed"
    raw_hash = outcome.model_calls[1].raw_output_hash
    assert raw_hash is not None
    assert raw_hash.startswith("sha256:")


@pytest.mark.parametrize("raw", ["", "   ", '{"steps":['])
def test_empty_or_truncated_json_exhaustion_is_typed(tmp_path: Path, raw: str) -> None:
    provider = MockLLM(responses_by_node={"create_plan": [raw, raw]})
    with (
        build_test_container(tmp_path / "artifacts") as container,
        pytest.raises(PlannerInvalidJsonError) as raised,
    ):
        _create(_planner(container, provider))

    assert raised.value.attempts == 2


def test_complete_markdown_json_fence_is_semantics_preserving(tmp_path: Path) -> None:
    raw = f"```json\n{_proposal().model_dump_json()}\n```"
    provider = MockLLM(responses_by_node={"create_plan": [raw]})
    with build_test_container(tmp_path / "artifacts") as container:
        outcome = _create(_planner(container, provider))

    assert outcome.validation.is_valid
    assert outcome.structured_output_retries == 0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["steps"][2].update({"depends_on": ["missing"]}),
        lambda payload: payload["steps"][0].update({"capability": "invented_tool"}),
    ],
)
def test_schema_invalid_proposal_gets_targeted_repair(
    tmp_path: Path,
    mutation: Any,
) -> None:
    invalid = _proposal().model_dump(mode="json")
    assert callable(mutation)
    mutation(invalid)
    provider = MockLLM(
        responses_by_node={
            "create_plan": [invalid],
            "repair_plan": [_proposal()],
        }
    )
    with build_test_container(tmp_path / "artifacts") as container:
        outcome = _create(_planner(container, provider))

    assert outcome.validation.is_valid
    assert outcome.repair_attempts == 1
    assert [call.context.node_name for call in provider.calls] == [
        "create_plan",
        "repair_plan",
    ]
    repair_payload = provider.calls[1].messages[1].content
    assert "validation_errors" in repair_payload
    assert "input_schema" not in repair_payload


def test_valid_proposal_with_missing_capability_gets_compiler_targeted_repair(
    tmp_path: Path,
) -> None:
    incomplete = _proposal().model_copy(update={"steps": _proposal().steps[:-1]})
    provider = MockLLM(
        responses_by_node={
            "create_plan": [incomplete],
            "repair_plan": [_proposal()],
        }
    )
    with build_test_container(tmp_path / "artifacts") as container:
        outcome = _create(_planner(container, provider))

    assert outcome.validation.is_valid
    assert outcome.repair_attempts == 1
    assert outcome.model_calls[1].repair_type == "targeted_plan_repair"


def test_targeted_repair_failure_exhausts_independent_budget(tmp_path: Path) -> None:
    incomplete = _proposal().model_copy(update={"steps": _proposal().steps[:-1]})
    provider = MockLLM(
        responses_by_node={
            "create_plan": [incomplete],
            "repair_plan": [incomplete],
        }
    )
    with (
        build_test_container(tmp_path / "artifacts") as container,
        pytest.raises(PlannerRepairExhaustedError) as raised,
    ):
        _create(_planner(container, provider, targeted_repairs=1))

    assert raised.value.attempts == 2
    assert len(provider.calls) == 2


def test_schema_failure_without_repair_budget_preserves_root_cause(tmp_path: Path) -> None:
    provider = MockLLM(responses_by_node={"create_plan": [{"steps": [{"step_id": "broken"}]}]})
    with (
        build_test_container(tmp_path / "artifacts") as container,
        pytest.raises(PlannerSchemaValidationError),
    ):
        _create(_planner(container, provider, targeted_repairs=0))


def test_provider_timeout_and_unavailable_are_distinct_planner_failures(
    tmp_path: Path,
) -> None:
    cases = (
        (LLMTimeoutError("timeout", attempts=3), PlannerTimeoutError),
        (LLMUnavailableError("unavailable", attempts=3), PlannerProviderError),
    )
    for index, (provider_error, expected) in enumerate(cases):
        provider = MockLLM(responses_by_node={"create_plan": [provider_error]})
        with (
            build_test_container(tmp_path / f"artifacts-{index}") as container,
            pytest.raises(expected) as raised,
        ):
            _create(_planner(container, provider))
        assert raised.value.attempts == 3


def test_execution_metadata_in_model_output_is_not_repaired_without_budget(
    tmp_path: Path,
) -> None:
    invalid = _proposal().model_dump(mode="json")
    invalid["steps"][0]["arguments"] = {
        "requires_approval": False,
        "risk_level": "LOW",
        "tool_version": "fake",
    }
    provider = MockLLM(responses_by_node={"create_plan": [invalid]})
    with (
        build_test_container(tmp_path / "artifacts") as container,
        pytest.raises(PlannerSchemaValidationError),
    ):
        _create(_planner(container, provider, targeted_repairs=0))
