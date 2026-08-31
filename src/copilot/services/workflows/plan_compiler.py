"""Deterministic compilation of untrusted ProposedPlan values into canonical TaskPlans."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from copilot.contracts import CapabilityName, ProposedPlan, TaskContract, TaskPlan, TaskType
from copilot.services.domains import (
    DomainCapabilityManifestRegistry,
    DomainManifestError,
    builtin_domain_manifest_registry,
)
from copilot.services.workflows.accounts_payable_plan import AccountsPayableAnalysisPlanFactory
from copilot.services.workflows.errors import (
    PlannerCompilationError,
    PlannerUnsupportedCapabilityError,
)
from copilot.services.workflows.fixed_plan import SupplierQualityAnalysisPlanFactory
from copilot.tools.exceptions import ToolRuntimeError
from copilot.tools.registry import ToolRegistry

_CONTRACT_AUTHORITY_ARGUMENTS = frozenset(
    {
        "artifact_type",
        "business_unit_ids",
        "currency_scope",
        "data_scope",
        "deadline_at",
        "end_date",
        "exception_types",
        "format",
        "language",
        "legal_entity_ids",
        "metrics",
        "output_format",
        "quarter",
        "report_format",
        "snapshot_at",
        "start_date",
        "supplier_ids",
        "time_range",
        "year",
    }
)


@dataclass(frozen=True, slots=True)
class PlanCompilationDiagnostic:
    """One bounded, non-sensitive deterministic compiler decision."""

    code: str
    message: str
    step_id: str | None = None
    field: str | None = None


@dataclass(frozen=True, slots=True)
class PlanCompilationResult:
    """Canonical executable plan plus suggestion-normalization diagnostics."""

    plan: TaskPlan
    diagnostics: tuple[PlanCompilationDiagnostic, ...] = ()


class PlanCompiler:
    """Resolve all executable metadata from trusted domain and Registry authorities."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        domain_manifests: DomainCapabilityManifestRegistry | None = None,
    ) -> None:
        self._registry = registry
        self._domain_manifests = domain_manifests or builtin_domain_manifest_registry()
        self._supplier = SupplierQualityAnalysisPlanFactory(registry)
        self._accounts_payable = AccountsPayableAnalysisPlanFactory(registry)

    def compile(
        self,
        proposed_plan: ProposedPlan,
        contract: TaskContract,
        *,
        planning_version: int,
        max_steps: int,
        created_at: datetime,
    ) -> PlanCompilationResult:
        """Compile a suggestion without trusting any model-provided execution fact."""
        if planning_version < 1:
            raise PlannerCompilationError("planning_version must be positive")
        try:
            manifest = self._domain_manifests.require_execution(contract)
        except DomainManifestError as exc:
            raise PlannerCompilationError(str(exc)) from exc

        suggested = tuple(step.capability for step in proposed_plan.steps)
        suggested_set = set(suggested)
        allowed_set = set(manifest.capabilities)
        unsupported = suggested_set - allowed_set
        if unsupported:
            names = ", ".join(sorted(item.value for item in unsupported))
            raise PlannerUnsupportedCapabilityError(
                f"Proposed plan contains capabilities outside the domain manifest: {names}"
            )
        if len(suggested) != len(suggested_set):
            raise PlannerCompilationError("Proposed plan must suggest each capability at most once")
        if suggested_set != allowed_set:
            missing = ", ".join(sorted(item.value for item in allowed_set - suggested_set))
            raise PlannerCompilationError(
                f"Proposed plan does not cover the complete governed capability set: {missing}"
            )

        diagnostics = [*self._dependency_diagnostics(proposed_plan)]
        for step in proposed_plan.steps:
            authoritative = sorted(_find_keys(step.arguments.root, _CONTRACT_AUTHORITY_ARGUMENTS))
            if authoritative:
                diagnostics.append(
                    PlanCompilationDiagnostic(
                        code="TASK_CONTRACT_ARGUMENT_OVERRIDDEN",
                        message=(
                            "TaskContract remains authoritative for: "
                            + ", ".join(authoritative[:8])
                        ),
                        step_id=step.step_id,
                        field="arguments",
                    )
                )
            elif step.arguments.root:
                diagnostics.append(
                    PlanCompilationDiagnostic(
                        code="PLANNER_ARGUMENT_NOT_EXECUTABLE",
                        message=(
                            "Semantic proposal arguments do not populate the executable TaskPlan; "
                            "runtime inputs remain deterministic"
                        ),
                        step_id=step.step_id,
                        field="arguments",
                    )
                )

        if contract.task_type is TaskType.SUPPLIER_QUALITY_ANALYSIS_V1:
            plan = self._supplier.compile(
                contract,
                planning_version=planning_version,
                created_at=created_at,
            )
        elif contract.task_type is TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1:
            plan = self._accounts_payable.compile(
                contract,
                planning_version=planning_version,
                created_at=created_at,
            )
        else:  # pragma: no cover - TaskType is closed, but fail closed if it expands.
            raise PlannerCompilationError("No canonical compiler exists for the task type")

        if len(plan.steps) > max_steps:
            raise PlannerCompilationError("Canonical plan exceeds the trusted maximum step count")
        self._verify_registry_bindings(plan)
        return PlanCompilationResult(plan=plan, diagnostics=tuple(diagnostics))

    def _verify_registry_bindings(self, plan: TaskPlan) -> None:
        """Prove every compiled field resolves to the same immutable Registry definition."""
        for step in plan.steps:
            try:
                definition = self._registry.get_profile(
                    step.tool_name,
                    step.tool_version,
                    step.contract_profile,
                ).definition
            except ToolRuntimeError as exc:
                raise PlannerCompilationError(
                    f"Canonical capability binding is unavailable for {step.tool_name}"
                ) from exc
            if (
                step.input_schema != definition.input_schema
                or step.output_schema != definition.output_schema
            ):
                raise PlannerCompilationError(
                    f"Canonical schemas drifted from the Registry for {step.tool_name}"
                )

    @staticmethod
    def _dependency_diagnostics(
        proposed_plan: ProposedPlan,
    ) -> tuple[PlanCompilationDiagnostic, ...]:
        """Report when frozen domain ordering replaces an LLM dependency suggestion."""
        by_id = {step.step_id: step for step in proposed_plan.steps}
        by_capability = {step.capability: step for step in proposed_plan.steps}
        expected = {
            CapabilityName.KNOWLEDGE_SEARCH: set(),
            CapabilityName.DATABASE_QUERY: set(),
            CapabilityName.ANALYSIS_ENGINE: {CapabilityName.DATABASE_QUERY},
            CapabilityName.REPORT_GENERATOR: {
                CapabilityName.KNOWLEDGE_SEARCH,
                CapabilityName.ANALYSIS_ENGINE,
            },
        }
        diagnostics: list[PlanCompilationDiagnostic] = []
        for capability, step in by_capability.items():
            actual = {by_id[identifier].capability for identifier in step.depends_on}
            if actual != expected[capability]:
                diagnostics.append(
                    PlanCompilationDiagnostic(
                        code="DOMAIN_DEPENDENCY_NORMALIZED",
                        message=(
                            "Frozen domain dependency invariants replaced the proposed dependency"
                        ),
                        step_id=step.step_id,
                        field="depends_on",
                    )
                )
        return tuple(diagnostics)


def _find_keys(value: object, keys: frozenset[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.casefold() in keys:
                found.add(key)
            found.update(_find_keys(nested, keys))
    elif isinstance(value, list):
        for nested in value:
            found.update(_find_keys(nested, keys))
    return found


__all__ = [
    "PlanCompilationDiagnostic",
    "PlanCompilationResult",
    "PlanCompiler",
]
