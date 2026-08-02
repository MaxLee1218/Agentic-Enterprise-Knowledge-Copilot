"""Deterministic v1.1 policy decisions and approval-action fingerprints."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

from copilot.contracts import JsonObject, TaskContract, TaskStep, ToolDefinition


class PolicyOutcome(StrEnum):
    """Structured pre-execution policy outcomes."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


@dataclass(frozen=True, slots=True)
class ApprovalPolicyDecision:
    """One explicit policy result consumed by the graph gate."""

    outcome: PolicyOutcome
    reason: str
    policy_id: str
    required_role: str | None = None
    controlled_scope: tuple[str, ...] = ()
    editable_fields: tuple[str, ...] = ()


class SupplierQualityApprovalPolicy:
    """Require the v1.1 task approval immediately before its database read."""

    def evaluate(
        self,
        *,
        contract: TaskContract,
        step: TaskStep,
        definition: ToolDefinition,
        arguments: JsonObject,
        has_current_plan_approval: bool = False,
    ) -> ApprovalPolicyDecision:
        """Return a deterministic allow/deny/approval decision for exact arguments."""
        del arguments
        if step.tool_name != definition.tool_name:
            return ApprovalPolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason="Plan step does not match the registered tool definition",
                policy_id=definition.approval_policy.policy_id,
            )
        requirement = contract.approval_requirement
        if (
            requirement.required
            and not has_current_plan_approval
            and step.tool_name != "knowledge_search"
        ):
            role = requirement.approver_role or definition.approval_policy.approver_role
            if role is None:
                return ApprovalPolicyDecision(
                    outcome=PolicyOutcome.DENY,
                    reason="Required approval has no authorized approver role",
                    policy_id=requirement.policy_id or definition.approval_policy.policy_id,
                )
            return ApprovalPolicyDecision(
                outcome=PolicyOutcome.REQUIRE_APPROVAL,
                reason="Contract requires approval before controlled database access",
                policy_id=requirement.policy_id or definition.approval_policy.policy_id,
                required_role=role,
                controlled_scope=requirement.controlled_scope or contract.constraints.data_scope,
                editable_fields=definition.approval_policy.editable_fields,
            )
        return ApprovalPolicyDecision(
            outcome=PolicyOutcome.ALLOW,
            reason="Action is pre-authorized within the frozen read-only scope",
            policy_id=definition.approval_policy.policy_id,
        )


def schema_fingerprint(definition: ToolDefinition) -> str:
    """Return a deterministic digest of one registered input schema."""
    return _digest(definition.input_schema.root)


def action_fingerprint(
    *,
    task_id: str,
    planning_version: int,
    step_id: str,
    tool_name: str,
    tool_version: str,
    input_schema_fingerprint: str,
    controlled_scope: tuple[str, ...],
    arguments: JsonObject,
) -> str:
    """Bind the exact canonical action approved under the frozen v1.1 envelope."""
    return _digest(
        {
            "task_id": task_id,
            "planning_version": planning_version,
            "step_id": step_id,
            "tool_name": tool_name,
            "tool_version": tool_version,
            "input_schema_fingerprint": input_schema_fingerprint,
            "controlled_scope": list(controlled_scope),
            "arguments": arguments.root,
        }
    )


def changed_top_level_fields(original: JsonObject, replacement: JsonObject) -> frozenset[str]:
    """Return all added, removed, or value-changed top-level fields."""
    keys = set(original.root) | set(replacement.root)
    return frozenset(key for key in keys if original.root.get(key) != replacement.root.get(key))


def arguments_fingerprint(arguments: JsonObject) -> str:
    """Return a payload-only digest suitable for redacted approval audit records."""
    return _digest(arguments.root)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ApprovalPolicyDecision",
    "PolicyOutcome",
    "SupplierQualityApprovalPolicy",
    "action_fingerprint",
    "arguments_fingerprint",
    "changed_top_level_fields",
    "schema_fingerprint",
]
