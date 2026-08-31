"""Deterministic planner manifest derived only from the governed ToolRegistry."""

from __future__ import annotations

from collections.abc import Callable

from copilot.contracts import CapabilityName, ToolDefinition
from copilot.llm.schemas import PlannerCapabilityManifest, PlannerCapabilityManifestEntry
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

    @property
    def registry(self) -> ToolRegistry:
        """Expose the injected Registry for deterministic PlanCompiler composition."""
        return self._registry

    def build(
        self, domain_manifest: DomainCapabilityManifest | None = None
    ) -> PlannerCapabilityManifest:
        """Return only semantic descriptions for enabled domain capabilities."""
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
            entries.append(
                PlannerCapabilityManifestEntry(
                    capability=capability,
                    description=definition.description[: self._max_description_length],
                    semantic_arguments=_SEMANTIC_ARGUMENTS[capability],
                )
            )
        return PlannerCapabilityManifest(
            task_type=selected.task_type,
            capabilities=tuple(entries),
        )


_SEMANTIC_ARGUMENTS = {
    CapabilityName.KNOWLEDGE_SEARCH: ("optional topic hint",),
    CapabilityName.DATABASE_QUERY: (),
    CapabilityName.ANALYSIS_ENGINE: (),
    CapabilityName.REPORT_GENERATOR: ("optional requested format echo",),
}


__all__ = ["PlannerToolManifestBuilder", "ToolVisibility"]
