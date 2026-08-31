"""Contract tests for the non-executable model-facing ProposedPlan."""

from typing import Any

import pytest
from pydantic import ValidationError

from copilot.contracts import CapabilityName, JsonObject, ProposedPlan, ProposedStep


def _payload() -> dict[str, Any]:
    return {
        "steps": [
            {
                "step_id": "knowledge",
                "capability": "knowledge_search",
                "purpose": "Find applicable policy evidence",
                "arguments": {},
                "depends_on": [],
            },
            {
                "step_id": "database",
                "capability": "database_query",
                "purpose": "Retrieve the governed business dataset",
                "arguments": {},
                "depends_on": [],
            },
            {
                "step_id": "analysis",
                "capability": "analysis_engine",
                "purpose": "Calculate deterministic metrics",
                "arguments": {},
                "depends_on": ["database"],
            },
            {
                "step_id": "report",
                "capability": "report_generator",
                "purpose": "Generate the requested internal report",
                "arguments": {"format": "PDF"},
                "depends_on": ["knowledge", "analysis"],
            },
        ]
    }


def test_valid_minimal_proposed_plan_round_trips() -> None:
    plan = ProposedPlan.model_validate(_payload())

    assert plan.steps[0].capability is CapabilityName.KNOWLEDGE_SEARCH
    assert ProposedPlan.model_validate_json(plan.model_dump_json()) == plan


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.pop("steps"), "steps"),
        (
            lambda payload: payload["steps"][0].update({"capability": "invented_tool"}),
            "capability",
        ),
        (
            lambda payload: payload["steps"][1].update({"step_id": "knowledge"}),
            "unique",
        ),
        (
            lambda payload: payload["steps"][2].update({"depends_on": ["missing"]}),
            "unknown proposed step dependencies",
        ),
        (
            lambda payload: payload["steps"][2].update({"depends_on": ["analysis"]}),
            "cannot depend on itself",
        ),
        (
            lambda payload: payload["steps"][1].update({"depends_on": ["analysis"]}),
            "acyclic",
        ),
        (
            lambda payload: payload["steps"][0].update({"arguments": []}),
            "arguments",
        ),
        (
            lambda payload: payload["steps"][0].update({"tool_version": "fake"}),
            "extra_forbidden",
        ),
        (
            lambda payload: payload["steps"][0].update(
                {"arguments": {"nested": {"requires_approval": False}}}
            ),
            "prohibited execution metadata",
        ),
    ],
)
def test_proposed_plan_rejects_invalid_or_authority_bearing_shapes(
    mutation: object,
    message: str,
) -> None:
    payload = _payload()
    assert callable(mutation)
    mutation(payload)

    with pytest.raises(ValidationError, match=message):
        ProposedPlan.model_validate(payload)


def test_duplicate_dependency_edges_are_normalized_without_semantic_guessing() -> None:
    payload = _payload()
    payload["steps"][2]["depends_on"] = ["database", "database"]

    plan = ProposedPlan.model_validate(payload)

    assert plan.steps[2].depends_on == ("database",)


def test_constructor_forbids_execution_metadata_in_arguments() -> None:
    with pytest.raises(ValidationError, match="risk_level"):
        ProposedStep(
            step_id="s1",
            capability=CapabilityName.KNOWLEDGE_SEARCH,
            purpose="Retrieve policy",
            arguments=JsonObject({"risk_level": "LOW"}),
        )


@pytest.mark.parametrize(
    "field",
    (
        "tool_version",
        "contract_profile",
        "input_schema",
        "output_schema",
        "requires_approval",
        "risk_level",
        "timeout_seconds",
        "retry_policy",
        "tenant_id",
        "role",
        "authorization_scope",
        "read_only",
        "permissions",
        "tenant",
        "write",
        "version",
    ),
)
def test_execution_authority_cannot_hide_in_semantic_arguments(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        ProposedStep(
            step_id="s1",
            capability=CapabilityName.DATABASE_QUERY,
            purpose="Retrieve governed data",
            arguments=JsonObject({field: False}),
        )
