"""Stage 5 contract and profile-resolution tests for AP analytics."""

from __future__ import annotations

import pytest

from copilot.contracts import JsonObject
from copilot.services.domains.manifests import ACCOUNTS_PAYABLE_MANIFEST
from copilot.tools.analytics import (
    AP_ANALYTICS_CONTRACT_PROFILE,
    AP_ANALYTICS_ENGINE_VERSION,
    AccountsPayableAnalyticsTool,
)
from copilot.tools.analytics.ap_schemas import APAnalyticsOperation, APDatabaseTemplate
from copilot.tools.analytics.exceptions import (
    APAnalyticsInputError,
    APAnalyticsOperationUnsupportedError,
)
from copilot.tools.exceptions import ToolNotFoundError
from copilot.tools.registry import ToolRegistry
from tests.unit.tools.analytics.ap_helpers import analytics_context, evidence_ledger


def test_ap_profile_keeps_stable_capability_after_stage8_execution_enablement() -> None:
    tool = AccountsPayableAnalyticsTool(evidence_ledger())
    registry = ToolRegistry()
    registry.register(tool, contract_profiles=(AP_ANALYTICS_CONTRACT_PROFILE,))

    resolved = registry.get_profile(
        "analysis_engine",
        tool.definition.tool_version,
        AP_ANALYTICS_CONTRACT_PROFILE,
    )

    assert resolved is tool
    assert tool.definition.tool_name == "analysis_engine"
    assert tool.definition.tool_version == "2.0.0-deterministic"
    assert AP_ANALYTICS_ENGINE_VERSION == "accounts_payable_analytics.v1"
    assert ACCOUNTS_PAYABLE_MANIFEST.execution_enabled is True
    assert len(APAnalyticsOperation) == 7
    assert len(APDatabaseTemplate) == 5
    with pytest.raises(ToolNotFoundError):
        registry.get_profile(
            "analysis_engine",
            tool.definition.tool_version,
            "supplier_quality_analytics.v1",
        )


def test_unknown_operation_and_loose_payload_fail_before_execution() -> None:
    tool = AccountsPayableAnalyticsTool(evidence_ledger())
    unknown = JsonObject(
        {
            "operation_name": "ap.fuzzy_duplicate.v1",
            "raw_sql": "SELECT * FROM invoices",
        }
    )
    with pytest.raises(APAnalyticsOperationUnsupportedError):
        tool.execute(unknown, analytics_context(unknown))

    missing = JsonObject({"operation_name": APAnalyticsOperation.EXCEPTION_SUMMARY.value})
    with pytest.raises(APAnalyticsInputError):
        tool.execute(missing, analytics_context(missing))
