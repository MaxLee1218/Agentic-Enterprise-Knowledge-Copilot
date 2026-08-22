"""Code-owned domain manifests that select exact governed contract profiles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel

from copilot.contracts import (
    ACCOUNTS_PAYABLE_CONTRACT_PROFILES,
    SUPPLIER_QUALITY_CONTRACT_PROFILES,
    AccountsPayableConstraintsV1,
    ArtifactType,
    CapabilityName,
    ContractSchemaVersion,
    SupplierQualityConstraintsV1,
    TaskContract,
    TaskType,
)


class DomainManifestError(ValueError):
    """Safe failure raised when a domain/profile is absent, inconsistent, or disabled."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CapabilityContractProfile:
    """Bind one stable capability name to one domain-specific contract profile."""

    capability: CapabilityName
    contract_profile: str


@dataclass(frozen=True, slots=True)
class DomainCapabilityManifest:
    """Small static selection record for one versioned business task type."""

    task_type: TaskType
    contract_schema_version: ContractSchemaVersion
    constraints_model: type[BaseModel]
    artifact_types: tuple[ArtifactType, ...]
    capability_profiles: tuple[CapabilityContractProfile, ...]
    understanding_profile: str
    plan_profile: str
    input_profile: str
    verifier_profile: str
    permission_purpose: str
    execution_enabled: bool

    @property
    def capabilities(self) -> tuple[CapabilityName, ...]:
        """Return the exact governed capability set in deterministic order."""
        return tuple(item.capability for item in self.capability_profiles)

    def profile_for(self, capability: CapabilityName | str) -> str:
        """Return the exact profile or fail closed for an unmanifested capability."""
        try:
            normalized = CapabilityName(capability)
        except ValueError as exc:
            raise DomainManifestError(
                "DOMAIN_CAPABILITY_NOT_ALLOWED",
                f"Capability {capability!s} is not governed for {self.task_type.value}",
            ) from exc
        for binding in self.capability_profiles:
            if binding.capability is normalized:
                return binding.contract_profile
        raise DomainManifestError(
            "DOMAIN_CAPABILITY_NOT_ALLOWED",
            f"Capability {normalized.value} is not governed for {self.task_type.value}",
        )


class DomainCapabilityManifestRegistry:
    """Immutable, deny-by-default registry keyed only by trusted versioned TaskType."""

    def __init__(self, manifests: tuple[DomainCapabilityManifest, ...]) -> None:
        by_type = {manifest.task_type: manifest for manifest in manifests}
        if len(by_type) != len(manifests):
            raise ValueError("domain manifest task types must be unique")
        self._manifests = by_type

    def resolve(self, task_type: TaskType | str) -> DomainCapabilityManifest:
        """Resolve one trusted type without fuzzy matching or fallback."""
        try:
            normalized = TaskType(task_type)
            return self._manifests[normalized]
        except (KeyError, ValueError) as exc:
            raise DomainManifestError(
                "DOMAIN_MANIFEST_NOT_FOUND",
                f"No governed domain manifest exists for {task_type!s}",
            ) from exc

    def validate_contract(self, contract: TaskContract) -> DomainCapabilityManifest:
        """Bind a validated Contract to the exact matching manifest and profiles."""
        manifest = self.resolve(contract.task_type)
        if contract.contract_schema_version is not manifest.contract_schema_version:
            raise DomainManifestError(
                "DOMAIN_CONTRACT_VERSION_MISMATCH",
                "Task contract schema version does not match its domain manifest",
            )
        if not isinstance(contract.constraints, manifest.constraints_model):
            raise DomainManifestError(
                "DOMAIN_CONSTRAINT_PROFILE_MISMATCH",
                "Task constraints do not match the selected domain manifest",
            )
        if contract.expected_output.artifact_type not in manifest.artifact_types:
            raise DomainManifestError(
                "DOMAIN_ARTIFACT_PROFILE_MISMATCH",
                "Task Artifact type does not match the selected domain manifest",
            )
        if contract.required_capabilities != manifest.capabilities:
            raise DomainManifestError(
                "DOMAIN_CAPABILITY_SET_MISMATCH",
                "Task capabilities do not exactly match the selected domain manifest",
            )
        if contract.missing_information:
            raise DomainManifestError(
                "TASK_INFORMATION_MISSING",
                "A contract with missing information cannot enter planning",
            )
        return manifest

    def require_execution(self, contract: TaskContract) -> DomainCapabilityManifest:
        """Return an enabled manifest or deny before planning/tool execution."""
        manifest = self.validate_contract(contract)
        if not manifest.execution_enabled:
            raise DomainManifestError(
                "DOMAIN_EXECUTION_NOT_ENABLED",
                f"Execution is not enabled for {manifest.task_type.value}",
            )
        return manifest

    def require_execution_for_type(
        self, task_type: TaskType | str
    ) -> DomainCapabilityManifest:
        """Deny disabled domains before an understanding adapter is invoked."""
        manifest = self.resolve(task_type)
        if not manifest.execution_enabled:
            raise DomainManifestError(
                "DOMAIN_EXECUTION_NOT_ENABLED",
                f"Execution is not enabled for {manifest.task_type.value}",
            )
        return manifest


def _profiles(values: Mapping[CapabilityName, str]) -> tuple[CapabilityContractProfile, ...]:
    return tuple(
        CapabilityContractProfile(capability, values[capability])
        for capability in CapabilityName
    )


SUPPLIER_QUALITY_MANIFEST = DomainCapabilityManifest(
    task_type=TaskType.SUPPLIER_QUALITY_ANALYSIS_V1,
    contract_schema_version=ContractSchemaVersion.TASK_CONTRACT_V1,
    constraints_model=SupplierQualityConstraintsV1,
    artifact_types=(
        ArtifactType.QUALITY_ANALYSIS_REPORT_PDF,
        ArtifactType.QUALITY_ANALYSIS_REPORT_JSON,
    ),
    capability_profiles=_profiles(SUPPLIER_QUALITY_CONTRACT_PROFILES),
    understanding_profile="supplier_quality_understanding.v1",
    plan_profile="supplier_quality_plan.v1",
    input_profile="supplier_quality_inputs.v1",
    verifier_profile="supplier_quality_verifier.v1",
    permission_purpose=TaskType.SUPPLIER_QUALITY_ANALYSIS_V1.value,
    execution_enabled=True,
)

ACCOUNTS_PAYABLE_MANIFEST = DomainCapabilityManifest(
    task_type=TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1,
    contract_schema_version=ContractSchemaVersion.TASK_CONTRACT_V2,
    constraints_model=AccountsPayableConstraintsV1,
    artifact_types=(
        ArtifactType.ACCOUNTS_PAYABLE_REPORT_PDF,
        ArtifactType.ACCOUNTS_PAYABLE_REPORT_JSON,
    ),
    capability_profiles=_profiles(ACCOUNTS_PAYABLE_CONTRACT_PROFILES),
    understanding_profile="accounts_payable_understanding.v1",
    plan_profile="accounts_payable_plan.v1",
    input_profile="accounts_payable_inputs.v1",
    verifier_profile="accounts_payable_verifier.v1",
    permission_purpose=TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1.value,
    execution_enabled=False,
)


def builtin_domain_manifest_registry() -> DomainCapabilityManifestRegistry:
    """Create the built-in registry; AP remains deliberately non-executable in Stage 1."""
    return DomainCapabilityManifestRegistry(
        (SUPPLIER_QUALITY_MANIFEST, ACCOUNTS_PAYABLE_MANIFEST)
    )


__all__ = [
    "ACCOUNTS_PAYABLE_MANIFEST",
    "CapabilityContractProfile",
    "DomainCapabilityManifest",
    "DomainCapabilityManifestRegistry",
    "DomainManifestError",
    "SUPPLIER_QUALITY_MANIFEST",
    "builtin_domain_manifest_registry",
]
