"""Deterministic planner manifest derived only from the governed ToolRegistry."""

from __future__ import annotations

from collections.abc import Callable

from copilot.contracts import ToolDefinition
from copilot.llm.schemas import PlannerToolManifest, PlannerToolManifestEntry
from copilot.services.domains import DomainCapabilityManifest, builtin_domain_manifest_registry
from copilot.tools.registry import ToolRegistry

ToolVisibility = Callable[[ToolDefinition], bool]


class PlannerToolManifestBuilder:
    """Build a stable, permission-filtered planner view without a second tool list."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        visibility: ToolVisibility | None = None,
        max_description_length: int = 500,
    ) -> None:
        self._registry = registry
        self._visibility = visibility or (lambda _definition: True)
        self._max_description_length = max_description_length

    def build(self, domain_manifest: DomainCapabilityManifest | None = None) -> PlannerToolManifest:
        """Return enabled visible definitions in the Registry's stable name order."""
        selected = domain_manifest or builtin_domain_manifest_registry().resolve(
            "supplier_quality_analysis.v1"
        )
        entries = []
        for capability in sorted(selected.capabilities, key=lambda item: item.value):
            profile = selected.profile_for(capability)
            registration = self._registry.profile_registration(capability.value, profile)
            definition = self._registry.get_profile(
                capability.value,
                registration.tool.definition.tool_version,
                profile,
            ).definition
            if not self._visibility(definition):
                continue
            side_effects = definition.idempotency.side_effects.strip().lower()
            read_only = (
                side_effects.startswith("none")
                or "read-only" in side_effects
                or "read only" in side_effects
                or "no business-data mutation" in side_effects
            )
            entries.append(
                PlannerToolManifestEntry(
                    name=definition.tool_name,
                    tool_version=definition.tool_version,
                    contract_profile=profile,
                    description=definition.description[: self._max_description_length],
                    input_schema=definition.input_schema,
                    output_schema=definition.output_schema,
                    risk_level=definition.risk_level,
                    read_only=read_only,
                    requires_approval=bool(
                        definition.approval_policy.trigger_conditions
                        or definition.approval_policy.approver_role
                    ),
                    idempotent=definition.idempotency.idempotent,
                )
            )
        return PlannerToolManifest(tools=tuple(entries))


__all__ = ["PlannerToolManifestBuilder", "ToolVisibility"]
