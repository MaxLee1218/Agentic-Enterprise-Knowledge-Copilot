"""Untrusted lightweight planning suggestions emitted by an LLM.

These contracts deliberately contain no executable tool metadata.  A proposed plan must be
compiled against the trusted domain manifest and Tool Registry before it can become a TaskPlan.
"""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from copilot.contracts.base import ImmutableContractModel, JsonObject
from copilot.contracts.enums import CapabilityName
from copilot.contracts.validators import validate_identifier

_EXECUTION_AUTHORITY_FIELDS = frozenset(
    {
        "allowed_roles",
        "allowed_tools",
        "approval",
        "approval_policy",
        "authorization_scope",
        "contract_profile",
        "data_scope",
        "idempotency",
        "idempotency_mode",
        "input_schema",
        "output_schema",
        "permission_metadata",
        "permission",
        "permissions",
        "profile",
        "read_only",
        "requires_approval",
        "retry",
        "max_attempts",
        "retry_policy",
        "risk",
        "risk_level",
        "role",
        "roles",
        "scope",
        "scopes",
        "schema",
        "schema_fingerprint",
        "tenant_id",
        "tenant",
        "tenant_scope",
        "timeout",
        "timeout_seconds",
        "version",
        "write",
        "tool_name",
        "tool_version",
    }
)


class ProposedStep(ImmutableContractModel):
    """One semantic capability suggestion with no execution authority."""

    step_id: str = Field(min_length=1, max_length=100)
    capability: CapabilityName
    purpose: str = Field(min_length=1, max_length=500)
    arguments: JsonObject = Field(default_factory=lambda: JsonObject({}))
    depends_on: tuple[str, ...] = ()

    _validate_step_id = field_validator("step_id")(validate_identifier)

    @field_validator("depends_on", mode="before")
    @classmethod
    def normalize_dependencies(cls, value: object) -> object:
        """Remove duplicate edges without changing their first-occurrence semantics."""
        if isinstance(value, (list, tuple)):
            return tuple(dict.fromkeys(value))
        return value

    @model_validator(mode="after")
    def reject_authority_and_self_dependency(self) -> ProposedStep:
        """Keep authorization metadata and direct cycles outside the suggestion contract."""
        if self.step_id in self.depends_on:
            raise ValueError("a proposed step cannot depend on itself")
        prohibited = sorted(_find_prohibited_keys(self.arguments.root))
        if prohibited:
            raise ValueError(
                "proposed arguments contain prohibited execution metadata: "
                + ", ".join(prohibited[:8])
            )
        return self


class ProposedPlan(ImmutableContractModel):
    """A non-executable, bounded DAG of semantic capability suggestions."""

    steps: tuple[ProposedStep, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_dag(self) -> ProposedPlan:
        """Require unique local step references and an acyclic suggestion graph."""
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("proposed step_id values must be unique")
        known = set(step_ids)
        graph = {step.step_id: step.depends_on for step in self.steps}
        for step in self.steps:
            missing = set(step.depends_on) - known
            if missing:
                raise ValueError(f"unknown proposed step dependencies: {sorted(missing)}")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("proposed plan dependencies must form an acyclic graph")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency_id in graph[step_id]:
                visit(dependency_id)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in step_ids:
            visit(step_id)
        return self


def _find_prohibited_keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.casefold() in _EXECUTION_AUTHORITY_FIELDS:
                found.add(key)
            found.update(_find_prohibited_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            found.update(_find_prohibited_keys(nested))
    return found


__all__ = ["ProposedPlan", "ProposedStep"]
