"""Historical JSON and checkpoint compatibility tests for Stage 1 profiles."""

import json
from pathlib import Path
from typing import Any

import pytest

from copilot.agent.state import checkpoint_serializer
from copilot.contracts import ContractSchemaVersion, TaskContract, TaskPlan, TaskStep
from copilot.contracts.serialization import (
    LEGACY_SCHEMA_VERSION_PREFIX,
    ContractUpcastError,
    deserialize_task_contract_json,
    deserialize_task_plan_json,
)
from copilot.tools.mock_supplier_quality import MockBehavior
from tests.unit.domain.helpers import make_contract
from tests.unit.evidence.helpers import valid_plan
from tests.workflow_helpers import build_test_container


def _legacy_contract_payload() -> dict[str, Any]:
    payload = make_contract().model_dump(mode="json")
    payload.pop("contract_schema_version")
    payload.pop("goal")
    payload.pop("missing_information")
    return payload


def _legacy_plan_payload() -> dict[str, Any]:
    payload = valid_plan().model_dump(mode="json")
    for step in payload["steps"]:
        step.pop("tool_version")
        step.pop("contract_profile")
    return payload


def test_legacy_contract_json_upcasts_only_to_task_contract_v1() -> None:
    payload = _legacy_contract_payload()
    restored = deserialize_task_contract_json(json.dumps(payload))

    assert restored.contract_schema_version is ContractSchemaVersion.TASK_CONTRACT_V1
    assert restored.task_type.value == payload["task_type"]
    assert restored.goal == "Analyze supplier quality and generate an evidence-backed report"
    assert restored.constraints.tenant_id == payload["constraints"]["tenant_id"]
    assert restored.missing_information == ()


def test_legacy_plan_upcasts_to_exact_schema_fingerprint_profiles(
    tmp_path: Path,
) -> None:
    restored = deserialize_task_plan_json(json.dumps(_legacy_plan_payload()))

    assert all(
        step.tool_version.startswith(LEGACY_SCHEMA_VERSION_PREFIX) for step in restored.steps
    )
    with build_test_container(
        tmp_path / "artifacts",
        analytics_behavior=MockBehavior(),
        report_behavior=MockBehavior(),
    ) as container:
        for step in restored.steps:
            resolved = container.registry.get_profile(
                step.tool_name,
                step.tool_version,
                step.contract_profile,
            )
            assert resolved.definition.input_schema == step.input_schema
            assert resolved.definition.output_schema == step.output_schema


def test_unknown_legacy_contract_shape_is_not_guessed() -> None:
    payload = _legacy_contract_payload()
    payload["future_scope_override"] = "all-tenants"

    with pytest.raises(ContractUpcastError, match="Unknown legacy TaskContract shape"):
        deserialize_task_contract_json(json.dumps(payload))


def test_unknown_legacy_step_schema_is_not_bound_to_latest_profile() -> None:
    payload = _legacy_plan_payload()
    payload["steps"][0]["input_schema"]["properties"]["unapproved"] = {"type": "string"}

    with pytest.raises(ContractUpcastError, match="fingerprint is not recognized"):
        deserialize_task_plan_json(json.dumps(payload))


def test_partial_profile_fields_are_rejected() -> None:
    payload = _legacy_plan_payload()
    payload["steps"][0]["tool_version"] = "latest"

    with pytest.raises(ContractUpcastError, match="partially define"):
        deserialize_task_plan_json(json.dumps(payload))


def test_checkpoint_serializer_upcasts_legacy_contract_and_plan() -> None:
    contract_payload = _legacy_contract_payload()
    legacy_contract = TaskContract.model_construct(
        **contract_payload,
        _fields_set=set(contract_payload),
    )
    legacy_contract.__dict__.pop("missing_information", None)
    plan_payload = _legacy_plan_payload()
    legacy_steps = tuple(
        TaskStep.model_construct(**step, _fields_set=set(step)) for step in plan_payload["steps"]
    )
    plan_payload["steps"] = legacy_steps
    legacy_plan = TaskPlan.model_construct(
        **plan_payload,
        _fields_set=set(plan_payload),
    )

    serializer = checkpoint_serializer()
    with pytest.warns(UserWarning, match="Pydantic serializer warnings"):
        encoded = serializer.dumps_typed({"contract": legacy_contract, "plan": legacy_plan})
    restored = serializer.loads_typed(encoded)

    assert isinstance(restored, dict)
    assert restored["contract"].contract_schema_version is (ContractSchemaVersion.TASK_CONTRACT_V1)
    assert restored["contract"].task_id == contract_payload["task_id"]
    assert isinstance(restored["plan"], TaskPlan)
    assert all(
        step.tool_version.startswith(LEGACY_SCHEMA_VERSION_PREFIX)
        for step in restored["plan"].steps
    )
