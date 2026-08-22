"""Stable domain contract-profile identifiers shared across platform boundaries."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from copilot.contracts.enums import CapabilityName

SUPPLIER_QUALITY_CONTRACT_PROFILES: Final = MappingProxyType(
    {
        CapabilityName.KNOWLEDGE_SEARCH: "supplier_quality_policy.v1",
        CapabilityName.DATABASE_QUERY: "supplier_quality_database.v1",
        CapabilityName.ANALYSIS_ENGINE: "supplier_quality_analytics.v1",
        CapabilityName.REPORT_GENERATOR: "supplier_quality_report.v1",
    }
)

ACCOUNTS_PAYABLE_CONTRACT_PROFILES: Final = MappingProxyType(
    {
        CapabilityName.KNOWLEDGE_SEARCH: "accounts_payable_policy.v1",
        CapabilityName.DATABASE_QUERY: "accounts_payable_database.v1",
        CapabilityName.ANALYSIS_ENGINE: "accounts_payable_analytics.v1",
        CapabilityName.REPORT_GENERATOR: "accounts_payable_report.v1",
    }
)


__all__ = [
    "ACCOUNTS_PAYABLE_CONTRACT_PROFILES",
    "SUPPLIER_QUALITY_CONTRACT_PROFILES",
]
