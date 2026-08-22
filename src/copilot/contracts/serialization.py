"""Version-aware TaskContract and TaskPlan deserialization with fail-closed upcasters."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, cast

from pydantic import BaseModel

from copilot.contracts import (
    SUPPLIER_QUALITY_CONTRACT_PROFILES,
    CapabilityName,
    ContractSchemaVersion,
    TaskContract,
    TaskPlan,
    TaskStep,
)

LEGACY_SCHEMA_VERSION_PREFIX = "legacy-schema-sha256:"
LEGACY_SUPPLIER_QUALITY_GOAL = "Analyze supplier quality and generate an evidence-backed report"

_LEGACY_CONTRACT_KEYS = frozenset(
    {
        "task_id",
        "contract_version",
        "task_type",
        "required_capabilities",
        "expected_output",
        "constraints",
        "approval_requirement",
        "created_at",
    }
)
_LEGACY_EXPECTED_OUTPUT_KEYS = frozenset(
    {"artifact_type", "required_sections", "language", "citations_required"}
)
_LEGACY_QUALITY_CONSTRAINT_REQUIRED_KEYS = frozenset(
    {
        "year",
        "quarter",
        "start_date",
        "end_date",
        "supplier_ids",
        "tenant_id",
        "data_scope",
        "metrics",
        "deadline_at",
    }
)
_LEGACY_APPROVAL_KEYS = frozenset({"required", "policy_id", "approver_role", "controlled_scope"})
_LEGACY_PLAN_KEYS = frozenset({"task_id", "steps", "planning_version", "created_at"})
_LEGACY_STEP_KEYS = frozenset(
    {
        "step_id",
        "task_id",
        "step_type",
        "tool_name",
        "input_schema",
        "output_schema",
        "dependency",
        "retry_policy",
    }
)

# Exact schema-pair fingerprints emitted by the implemented production and offline Supplier
# Quality v1 adapters before TaskStep acquired explicit version/profile fields.
_LEGACY_QUALITY_SCHEMA_BINDINGS = {
    "059370422a448486a69bc842e3600adf58d846ee627e0dbda44753127842bd8f": (
        CapabilityName.KNOWLEDGE_SEARCH,
        SUPPLIER_QUALITY_CONTRACT_PROFILES[CapabilityName.KNOWLEDGE_SEARCH],
    ),
    "3c516d0502de5b430b8f0c9b30bb228f6663ee5532410d3e7ab1e0dbe7100050": (
        CapabilityName.KNOWLEDGE_SEARCH,
        SUPPLIER_QUALITY_CONTRACT_PROFILES[CapabilityName.KNOWLEDGE_SEARCH],
    ),
    "c6f6ea24d57737d1b247a7a50c98c9f106b97ea7431b59329b4bf6012ecc241f": (
        CapabilityName.DATABASE_QUERY,
        SUPPLIER_QUALITY_CONTRACT_PROFILES[CapabilityName.DATABASE_QUERY],
    ),
    "839a778dc51d9fb2bceee97548ca509f1b0baf106d5e76349da32f7f99ba9d6d": (
        CapabilityName.DATABASE_QUERY,
        SUPPLIER_QUALITY_CONTRACT_PROFILES[CapabilityName.DATABASE_QUERY],
    ),
    "e49281be49b0a1eccaab63906d95e5469949db61769f853da3adc21076243cb3": (
        CapabilityName.ANALYSIS_ENGINE,
        SUPPLIER_QUALITY_CONTRACT_PROFILES[CapabilityName.ANALYSIS_ENGINE],
    ),
    "2c6557422c6a6cd91b1f86a724981bafda5679fd9ed83eee0f1fba490591303f": (
        CapabilityName.ANALYSIS_ENGINE,
        SUPPLIER_QUALITY_CONTRACT_PROFILES[CapabilityName.ANALYSIS_ENGINE],
    ),
    "c09893ec3c25324c1f3d31f145051200c927c5c7fb680959d9bda2de5ffe3964": (
        CapabilityName.REPORT_GENERATOR,
        SUPPLIER_QUALITY_CONTRACT_PROFILES[CapabilityName.REPORT_GENERATOR],
    ),
    "88807831b991170b26b95dd44a7de60b24611bb2e4163b07a69268350927d05d": (
        CapabilityName.REPORT_GENERATOR,
        SUPPLIER_QUALITY_CONTRACT_PROFILES[CapabilityName.REPORT_GENERATOR],
    ),
}


class ContractUpcastError(ValueError):
    """Reject unknown or ambiguous historical payloads instead of guessing a profile."""


def deserialize_task_contract_json(payload: str) -> TaskContract:
    """Load current JSON or upcast one exact historical Supplier Quality contract."""
    raw = _json_object(payload, boundary="TaskContract")
    return TaskContract.model_validate(upcast_task_contract_payload(raw))


def deserialize_task_plan_json(payload: str) -> TaskPlan:
    """Load current JSON or upcast exact historical Supplier Quality step schemas."""
    raw = _json_object(payload, boundary="TaskPlan")
    return TaskPlan.model_validate(upcast_task_plan_payload(raw))


def upcast_task_contract_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Add `task-contract.v1` only to the exact known legacy Quality shape."""
    raw = dict(payload)
    if "contract_schema_version" in raw:
        return raw
    if frozenset(raw) != _LEGACY_CONTRACT_KEYS:
        raise ContractUpcastError("Unknown legacy TaskContract shape")
    if raw.get("task_type") != "supplier_quality_analysis.v1":
        raise ContractUpcastError("Only historical Supplier Quality contracts may be upcast")
    expected = _mapping(raw.get("expected_output"), boundary="ExpectedOutput")
    if frozenset(expected) != _LEGACY_EXPECTED_OUTPUT_KEYS:
        raise ContractUpcastError("Unknown legacy ExpectedOutput shape")
    if expected.get("artifact_type") not in {
        "QUALITY_ANALYSIS_REPORT_PDF",
        "QUALITY_ANALYSIS_REPORT_JSON",
    }:
        raise ContractUpcastError("Legacy contract has a non-Quality Artifact type")
    constraints = _mapping(raw.get("constraints"), boundary="TaskConstraints")
    constraint_keys = frozenset(constraints)
    if not _LEGACY_QUALITY_CONSTRAINT_REQUIRED_KEYS.issubset(constraint_keys):
        raise ContractUpcastError("Legacy Supplier Quality constraints are incomplete")
    if constraint_keys - (_LEGACY_QUALITY_CONSTRAINT_REQUIRED_KEYS | {"max_cost"}):
        raise ContractUpcastError("Unknown legacy Supplier Quality constraint field")
    approval = _mapping(raw.get("approval_requirement"), boundary="ApprovalRequirement")
    if frozenset(approval) != _LEGACY_APPROVAL_KEYS:
        raise ContractUpcastError("Unknown legacy ApprovalRequirement shape")
    raw["contract_schema_version"] = ContractSchemaVersion.TASK_CONTRACT_V1.value
    raw["goal"] = LEGACY_SUPPLIER_QUALITY_GOAL
    return raw


def upcast_task_plan_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Bind all legacy steps to exact known Quality schema fingerprints."""
    raw = dict(payload)
    steps = raw.get("steps")
    if not isinstance(steps, (list, tuple)):
        raise ContractUpcastError("TaskPlan steps must be a sequence")
    step_mappings = [_mapping(step, boundary="TaskStep") for step in steps]
    legacy = [
        "tool_version" not in step and "contract_profile" not in step for step in step_mappings
    ]
    current = ["tool_version" in step and "contract_profile" in step for step in step_mappings]
    if all(current):
        return raw
    if not all(legacy):
        raise ContractUpcastError("TaskPlan cannot mix or partially define profile fields")
    if frozenset(raw) != _LEGACY_PLAN_KEYS:
        raise ContractUpcastError("Unknown legacy TaskPlan shape")
    raw["steps"] = [upcast_legacy_task_step_payload(step) for step in step_mappings]
    return raw


def upcast_legacy_task_step_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Bind one old step only when its complete schema pair is explicitly known."""
    raw = dict(payload)
    if frozenset(raw) != _LEGACY_STEP_KEYS:
        raise ContractUpcastError("Unknown legacy TaskStep shape")
    tool_name = raw.get("tool_name")
    if not isinstance(tool_name, str):
        raise ContractUpcastError("Legacy TaskStep tool_name is invalid")
    input_schema = _schema_mapping(raw.get("input_schema"), boundary="input_schema")
    output_schema = _schema_mapping(raw.get("output_schema"), boundary="output_schema")
    digest = tool_schema_pair_fingerprint(tool_name, input_schema, output_schema)
    binding = _LEGACY_QUALITY_SCHEMA_BINDINGS.get(digest)
    if binding is None or binding[0].value != tool_name:
        raise ContractUpcastError("Legacy TaskStep schema fingerprint is not recognized")
    raw["tool_version"] = f"{LEGACY_SCHEMA_VERSION_PREFIX}{digest}"
    raw["contract_profile"] = binding[1]
    return raw


def tool_schema_pair_fingerprint(
    tool_name: str,
    input_schema: Mapping[str, Any],
    output_schema: Mapping[str, Any],
) -> str:
    """Return the canonical fingerprint used by historical step migration."""
    encoded = json.dumps(
        {
            "tool_name": tool_name,
            "input_schema": input_schema,
            "output_schema": output_schema,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def is_recognized_legacy_schema_binding(
    tool_name: str,
    schema_fingerprint: str,
    contract_profile: str,
) -> bool:
    """Return whether a legacy alias is explicitly bound to this Quality profile."""
    binding = _LEGACY_QUALITY_SCHEMA_BINDINGS.get(schema_fingerprint)
    return bool(
        binding is not None and binding[0].value == tool_name and binding[1] == contract_profile
    )


def upgrade_checkpoint_value(value: Any) -> Any:
    """Recursively repair legacy Pydantic checkpoint values after safe decoding."""
    if isinstance(value, TaskContract):
        if "contract_schema_version" in value.model_fields_set:
            return value
        raw = {key: item for key, item in value.__dict__.items() if key in value.model_fields_set}
        return TaskContract.model_validate(upcast_task_contract_payload(raw))
    if isinstance(value, TaskStep):
        if {"tool_version", "contract_profile"}.issubset(value.model_fields_set):
            return value
        raw = {key: item for key, item in value.__dict__.items() if key in value.model_fields_set}
        return TaskStep.model_validate(upcast_legacy_task_step_payload(raw))
    if isinstance(value, TaskPlan):
        upgraded_steps = tuple(_upgrade_checkpoint_step(step) for step in value.steps)
        if all(
            upgraded is original
            for upgraded, original in zip(upgraded_steps, value.steps, strict=True)
        ):
            return value
        raw = dict(value.__dict__)
        raw["steps"] = upgraded_steps
        return TaskPlan.model_validate(raw)
    if isinstance(value, dict):
        return {key: upgrade_checkpoint_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [upgrade_checkpoint_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(upgrade_checkpoint_value(item) for item in value)
    return value


def _upgrade_checkpoint_step(value: Any) -> TaskStep:
    if isinstance(value, TaskStep):
        upgraded = upgrade_checkpoint_value(value)
        assert isinstance(upgraded, TaskStep)
        return upgraded
    raw = dict(_mapping(value, boundary="checkpoint TaskStep"))
    if {"tool_version", "contract_profile"}.issubset(raw):
        return TaskStep.model_validate(raw)
    return TaskStep.model_validate(upcast_legacy_task_step_payload(raw))


def _json_object(payload: str, *, boundary: str) -> dict[str, Any]:
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ContractUpcastError(f"{boundary} JSON is invalid") from exc
    return dict(_mapping(raw, boundary=boundary))


def _mapping(value: Any, *, boundary: str) -> Mapping[str, Any]:
    if isinstance(value, BaseModel):
        return cast(Mapping[str, Any], value.model_dump(mode="json", exclude_unset=True))
    if isinstance(value, Mapping):
        return value
    raise ContractUpcastError(f"{boundary} must be an object")


def _schema_mapping(value: Any, *, boundary: str) -> Mapping[str, Any]:
    if hasattr(value, "root"):
        value = value.root
    return _mapping(value, boundary=boundary)


__all__ = [
    "ContractUpcastError",
    "LEGACY_SUPPLIER_QUALITY_GOAL",
    "LEGACY_SCHEMA_VERSION_PREFIX",
    "deserialize_task_contract_json",
    "deserialize_task_plan_json",
    "is_recognized_legacy_schema_binding",
    "tool_schema_pair_fingerprint",
    "upcast_legacy_task_step_payload",
    "upcast_task_contract_payload",
    "upcast_task_plan_payload",
    "upgrade_checkpoint_value",
]
