"""Deterministic Stage 7 Accounts Payable report fixtures."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

from copilot.contracts import (
    AccountsPayableConstraintsV1,
    ApprovalRequirement,
    ArtifactType,
    CapabilityName,
    ContractSchemaVersion,
    DateRange,
    ExpectedOutput,
    JsonObject,
    MoneyThreshold,
    ReportLanguage,
    TaskContract,
    TaskType,
    ToolCall,
)
from copilot.contracts.base import JsonMapping
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.persistence.artifact_repository import LocalArtifactRepository
from copilot.tools.analytics import AccountsPayableAnalyticsTool
from copilot.tools.analytics.ap_schemas import (
    APAnalyticsOperation,
    APAnalyticsResultV1,
    APDatabaseTemplate,
    APPolicyRuleSnapshotV1,
)
from copilot.tools.base import ToolExecutionContext
from copilot.tools.reporting import (
    AccountsPayableReportComposer,
    AccountsPayableReportTool,
    APDetailAccess,
    APReportRequestV1,
    APReportScopeV1,
)
from copilot.tools.reporting.schemas import ReportFormat
from tests.unit.tools.analytics.ap_helpers import (
    FIXED_TIME,
    add_database_dataset,
    add_policy_evidence,
    aggregation_arguments,
    analytics_context,
    detection_arguments,
    duplicate_row,
    evidence_ledger,
    payment_amount_row,
    payment_terms_row,
    po_row,
    population_row,
)

TASK_ID = "T-AP-AN-001"
TENANT_ID = "TENANT-DEMO"
AP_REQUIRED_SECTIONS = (
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
)


class SequentialIds:
    """Predictable Artifact identifier source."""

    def __init__(self) -> None:
        self._value = 0

    def new_id(self, prefix: str) -> str:
        self._value += 1
        return f"{prefix}-AP-{self._value:04d}"


def ap_report_fixture() -> tuple[
    InMemoryEvidenceLedger,
    APReportRequestV1,
]:
    """Build all requested AP operations and a complete governed report request."""
    ledger = evidence_ledger()
    rule_snapshot_payload, _document_ids = add_policy_evidence(ledger)
    population = [
        population_row("I-001", payment_count=1, settled_payment_count=1),
        population_row(
            "I-002",
            payment_count=1,
            settled_payment_count=1,
            po_record_key=None,
            po_matching_basis=None,
            po_status=None,
        ),
    ]
    datasets = {
        APDatabaseTemplate.INVOICE_POPULATION: add_database_dataset(
            ledger, APDatabaseTemplate.INVOICE_POPULATION, population
        ),
        APDatabaseTemplate.DUPLICATE_CANDIDATES: add_database_dataset(
            ledger,
            APDatabaseTemplate.DUPLICATE_CANDIDATES,
            [duplicate_row(row) for row in population],
        ),
        APDatabaseTemplate.INVOICE_PO_VARIANCE: add_database_dataset(
            ledger,
            APDatabaseTemplate.INVOICE_PO_VARIANCE,
            [
                po_row(population[0], approved_amount="800.0000"),
                po_row(population[1], po_record_key=None),
            ],
        ),
        APDatabaseTemplate.PAYMENT_TERMS: add_database_dataset(
            ledger,
            APDatabaseTemplate.PAYMENT_TERMS,
            [payment_terms_row(row, payment_date="2026-06-05") for row in population],
        ),
        APDatabaseTemplate.PAYMENT_AMOUNT: add_database_dataset(
            ledger,
            APDatabaseTemplate.PAYMENT_AMOUNT,
            [payment_amount_row(row, payment_amount="1200.0000") for row in population],
        ),
    }
    operation_templates = {
        APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION: (
            APDatabaseTemplate.DUPLICATE_CANDIDATES
        ),
        APAnalyticsOperation.INVOICE_PO_VARIANCE_DETECTION: (
            APDatabaseTemplate.INVOICE_PO_VARIANCE
        ),
        APAnalyticsOperation.MISSING_PO_DETECTION: APDatabaseTemplate.INVOICE_PO_VARIANCE,
        APAnalyticsOperation.PAYMENT_TERM_COMPLIANCE_DETECTION: APDatabaseTemplate.PAYMENT_TERMS,
        APAnalyticsOperation.OVERPAYMENT_DETECTION: APDatabaseTemplate.PAYMENT_AMOUNT,
    }
    analytics = AccountsPayableAnalyticsTool(ledger)
    calculation_ids: list[str] = []
    for index, (operation, template) in enumerate(operation_templates.items(), start=1):
        arguments = detection_arguments(
            operation,
            datasets[APDatabaseTemplate.INVOICE_POPULATION],
            datasets[template],
            rule_snapshot_payload,
        )
        context = analytics_context(arguments, call_id=f"TC-AP-REPORT-DET-{index}")
        execution = analytics.execute(arguments, context)
        calculation_ids.extend(
            item.evidence_id for item in ledger.record(context.call, execution.evidence)
        )

    summary_result: APAnalyticsResultV1 | None = None
    for operation in (
        APAnalyticsOperation.EXCEPTION_SUMMARY,
        APAnalyticsOperation.SUPPLIER_EXCEPTION_RATE,
    ):
        arguments = aggregation_arguments(
            operation,
            datasets[APDatabaseTemplate.INVOICE_POPULATION],
            rule_snapshot_payload,
            tuple(calculation_ids),
        )
        label = "SUMMARY" if operation is APAnalyticsOperation.EXCEPTION_SUMMARY else "SUPPLIER"
        context = analytics_context(arguments, call_id=f"TC-AP-REPORT-{label}")
        execution = analytics.execute(arguments, context)
        ledger.record(context.call, execution.evidence)
        if operation is APAnalyticsOperation.EXCEPTION_SUMMARY:
            summary_result = APAnalyticsResultV1.model_validate(execution.output.root)
    assert summary_result is not None
    evidence_ids = tuple(item.evidence_id for item in ledger.list(TASK_ID, tenant_id=TENANT_ID))
    rule_snapshot = APPolicyRuleSnapshotV1.model_validate(rule_snapshot_payload)
    request = APReportRequestV1(
        task_id=TASK_ID,
        scope=APReportScopeV1(
            start_date=date(2026, 4, 1),
            end_date=date(2026, 6, 30),
            supplier_ids=(),
            legal_entity_ids=("LE-US-01",),
            business_unit_ids=(),
            currency_scope=(),
        ),
        exception_summary_result=summary_result,
        evidence_refs=evidence_ids,
        policy_rule_snapshot=rule_snapshot,
        template_version="accounts_payable_report.v1",
        format=ReportFormat.JSON,
        language=ReportLanguage.EN_US,
        detail_access=APDetailAccess.DETAIL,
    )
    return ledger, request


def ap_report_context(request: APReportRequestV1) -> ToolExecutionContext:
    """Bind one report request to the exact trusted AP purpose and detail scope."""
    arguments = JsonObject(cast(JsonMapping, request.model_dump(mode="json")))
    return ToolExecutionContext(
        call=ToolCall(
            tool_call_id="TC-AP-REPORT",
            task_id=TASK_ID,
            step_id="S-AP-REPORT",
            tool_name="report_generator",
            tool_version=AccountsPayableReportTool.definition.tool_version,
            input=arguments,
            idempotency_key=f"AP-REPORT-{request.format.value}-{request.detail_access.value}",
            approval_id=None,
            deadline_at=FIXED_TIME + timedelta(minutes=2),
            tenant_id=TENANT_ID,
            user_id="U-FINANCE-001",
        ),
        trace_id="TRACE-AP-REPORT",
        tenant_id=TENANT_ID,
        user_id="U-FINANCE-001",
        roles=("finance_analyst",),
        scopes=("finance:ap.detail", "finance:ap.artifact:download", "artifact.write"),
        purpose="accounts_payable_analysis.v1",
    )


def ap_task_contract(request: APReportRequestV1) -> TaskContract:
    """Build the Stage 6 verifier contract matching the generated Stage 7 report."""
    snapshot_at = datetime(2026, 10, 1, tzinfo=UTC)
    return TaskContract(
        contract_schema_version=ContractSchemaVersion.TASK_CONTRACT_V2,
        task_id=TASK_ID,
        contract_version=1,
        task_type=TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1,
        goal="Verify the deterministic Accounts Payable report",
        required_capabilities=tuple(CapabilityName),
        expected_output=ExpectedOutput(
            artifact_type=ArtifactType.ACCOUNTS_PAYABLE_REPORT_JSON,
            required_sections=AP_REQUIRED_SECTIONS,
            language=ReportLanguage.EN_US,
            citations_required=True,
        ),
        constraints=AccountsPayableConstraintsV1(
            time_range=DateRange(
                start_date=request.scope.start_date,
                end_date=request.scope.end_date,
            ),
            supplier_ids=request.scope.supplier_ids,
            legal_entity_ids=request.scope.legal_entity_ids,
            business_unit_ids=request.scope.business_unit_ids,
            currency_scope=request.scope.currency_scope,
            effective_materiality=(
                MoneyThreshold(currency="CNY", amount=Decimal("5000.0000")),
                MoneyThreshold(currency="USD", amount=Decimal("1000.0000")),
            ),
            tenant_id=TENANT_ID,
            data_scope=("accounts_payable.v1", "accounts-payable-policy-v1"),
            policy_rule_set_id="accounts-payable-v1",
            policy_rule_set_version="ap_rules.2026.1",
            policy_manifest_checksum=(request.policy_rule_snapshot.rule_manifest.manifest_checksum),
            snapshot_at=snapshot_at,
            deadline_at=snapshot_at + timedelta(minutes=5),
        ),
        approval_requirement=ApprovalRequirement(required=False),
        created_at=FIXED_TIME,
    )


def ap_report_tool(
    root: Path,
    ledger: InMemoryEvidenceLedger,
    *,
    max_size_bytes: int = 30 * 1024 * 1024,
) -> tuple[AccountsPayableReportTool, LocalArtifactRepository]:
    """Create the composable Stage 7 report profile with deterministic dependencies."""
    repository = LocalArtifactRepository(
        root,
        clock=lambda: FIXED_TIME,
        max_size_bytes=max_size_bytes,
    )
    tool = AccountsPayableReportTool(
        evidence_reader=ledger,
        artifact_store=repository,
        clock=lambda: FIXED_TIME,
        ids=SequentialIds(),
        composer=AccountsPayableReportComposer(ledger, clock=lambda: FIXED_TIME),
    )
    return tool, repository


__all__ = [
    "AP_REQUIRED_SECTIONS",
    "FIXED_TIME",
    "TASK_ID",
    "TENANT_ID",
    "ap_report_context",
    "ap_report_fixture",
    "ap_report_tool",
    "ap_task_contract",
]
