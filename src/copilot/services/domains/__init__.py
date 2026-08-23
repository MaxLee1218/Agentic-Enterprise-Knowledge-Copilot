"""Static, deny-by-default domain capability manifests."""

from copilot.services.domains.accounts_payable_inputs import (
    AP_DATABASE_TEMPLATE_IDS,
    APDatabaseTemplateId,
    build_accounts_payable_database_input,
)
from copilot.services.domains.manifests import (
    CapabilityContractProfile,
    DomainCapabilityManifest,
    DomainCapabilityManifestRegistry,
    DomainManifestError,
    builtin_domain_manifest_registry,
)

__all__ = [
    "AP_DATABASE_TEMPLATE_IDS",
    "APDatabaseTemplateId",
    "CapabilityContractProfile",
    "DomainCapabilityManifest",
    "DomainCapabilityManifestRegistry",
    "DomainManifestError",
    "build_accounts_payable_database_input",
    "builtin_domain_manifest_registry",
]
