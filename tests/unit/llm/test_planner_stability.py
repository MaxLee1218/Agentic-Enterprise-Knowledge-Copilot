"""Deterministic CI stability gate for both governed planning profiles."""

from pathlib import Path

import pytest

from copilot.llm.manifest import PlannerToolManifestBuilder
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.llm.planning import LLMPlanningService
from copilot.services.workflows.validation import PlanValidator
from tests.unit.domain.ap_helpers import make_ap_contract
from tests.unit.domain.helpers import make_contract
from tests.workflow_helpers import build_test_container, fixed_clock


@pytest.mark.parametrize(
    ("contract_factory", "max_steps", "expected_steps"),
    (
        (make_contract, 10, 4),
        (make_ap_contract, 14, 14),
    ),
)
def test_one_hundred_proposals_compile_to_the_same_valid_canonical_plan(
    tmp_path: Path,
    contract_factory: object,
    max_steps: int,
    expected_steps: int,
) -> None:
    assert callable(contract_factory)
    contract = contract_factory()
    with build_test_container(tmp_path / "artifacts") as container:
        planner = LLMPlanningService(
            provider=OfflineMockLLM(),
            manifest_builder=PlannerToolManifestBuilder(container.registry),
            validator=PlanValidator(
                registry=container.registry,
                max_task_steps=max_steps,
            ),
            clock=fixed_clock,
        )
        outcomes = tuple(
            planner.create_plan(
                contract=contract,
                trace_id=f"TRACE-STABILITY-{index}",
                max_steps=max_steps,
            )
            for index in range(100)
        )

    first = outcomes[0].plan
    assert all(outcome.validation.is_valid for outcome in outcomes)
    assert all(outcome.plan == first for outcome in outcomes)
    assert all(len(outcome.plan.steps) == expected_steps for outcome in outcomes)
    assert all(outcome.repair_attempts == 0 for outcome in outcomes)
    assert all(outcome.structured_output_retries == 0 for outcome in outcomes)
    assert all(outcome.model_calls[0].raw_output_chars > 0 for outcome in outcomes)
