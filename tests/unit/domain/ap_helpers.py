"""Typed Accounts Payable v1 contract builders for Stage 1 tests."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from copilot.contracts import (
    AccountsPayableConstraintsV1,
    ApprovalRequirement,
    ArtifactType,
    CapabilityName,
    ContractSchemaVersion,
    DateRange,
    ExpectedOutput,
    MoneyThreshold,
    ReportLanguage,
    TaskContract,
    TaskType,
)

SNAPSHOT_AT = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)


def make_ap_contract() -> TaskContract:
    """Return one executable-shape AP contract without enabling AP execution."""
    return TaskContract(
        contract_schema_version=ContractSchemaVersion.TASK_CONTRACT_V2,
        task_id="T-AP-001",
        contract_version=1,
        task_type=TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1,
        goal="Investigate Accounts Payable compliance exceptions",
        required_capabilities=tuple(CapabilityName),
        expected_output=ExpectedOutput(
            artifact_type=ArtifactType.ACCOUNTS_PAYABLE_REPORT_PDF,
            required_sections=(
                "scope",
                "data_overview",
                "applicable_policies",
                "exception_summary",
                "duplicate_invoice_findings",
                "po_compliance_findings",
                "payment_findings",
                "supplier_summary",
                "risk_observations",
                "recommended_actions",
                "limitations",
                "evidence",
                "execution_trace",
            ),
            language=ReportLanguage.EN_US,
            citations_required=True,
        ),
        constraints=AccountsPayableConstraintsV1(
            time_range=DateRange(
                start_date=date(2026, 4, 1),
                end_date=date(2026, 6, 30),
            ),
            supplier_ids=("SUP-001", "SUP-002"),
            legal_entity_ids=("LE-001",),
            business_unit_ids=("BU-001",),
            currency_scope=("USD", "CNY"),
            requested_materiality=(
                MoneyThreshold(currency="USD", amount=Decimal("5000.0000")),
            ),
            effective_materiality=(
                MoneyThreshold(currency="USD", amount=Decimal("5000.0000")),
                MoneyThreshold(currency="CNY", amount=Decimal("30000.0000")),
            ),
            tenant_id="TENANT-A",
            data_scope=("accounts_payable.v1", "accounts-payable-policy-v1"),
            policy_rule_set_id="accounts-payable-v1",
            policy_rule_set_version="ap_rules.2026.1",
            policy_manifest_checksum="sha256:ap-rules-2026-1",
            snapshot_at=SNAPSHOT_AT,
            deadline_at=SNAPSHOT_AT + timedelta(minutes=3),
            read_only=True,
        ),
        approval_requirement=ApprovalRequirement(required=False),
        created_at=SNAPSHOT_AT,
    )


__all__ = ["SNAPSHOT_AT", "make_ap_contract"]
