"""Static, deny-by-default domain capability manifests."""

from copilot.services.domains.manifests import (
    CapabilityContractProfile,
    DomainCapabilityManifest,
    DomainCapabilityManifestRegistry,
    DomainManifestError,
    builtin_domain_manifest_registry,
)

__all__ = [
    "CapabilityContractProfile",
    "DomainCapabilityManifest",
    "DomainCapabilityManifestRegistry",
    "DomainManifestError",
    "builtin_domain_manifest_registry",
]
