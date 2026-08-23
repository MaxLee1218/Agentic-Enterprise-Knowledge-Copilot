"""Controlled AP analytics request and Evidence builders shared by Stage 5 tests."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from copilot.contracts import (
    EvidenceContent,
    EvidenceItem,
    EvidenceSourceReference,
    EvidenceType,
    JsonObject,
    ToolCall,
)
from copilot.contracts.base import JsonMapping
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.tools.analytics import AccountsPayableAnalyticsTool
from copilot.tools.analytics.ap_schemas import APAnalyticsOperation, APDatabaseTemplate
from copilot.tools.base import ToolExecutionContext
from copilot.tools.knowledge import load_ap_policy_bundle

PROJECT_ROOT = Path(__file__).resolve().parents[4]
AP_POLICY_ROOT = PROJECT_ROOT / "data" / "policies" / "accounts_payable" / "v1"
FIXED_TIME = datetime(2026, 8, 23, tzinfo=UTC)
_ID_SEQUENCE = count(1)


def checksum(value: object, *, prefixed: bool = False) -> str:
    """Return the canonical checksum used by the AP Database/Analytics boundary."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    return f"sha256:{digest}" if prefixed else digest


def population_row(
    key: str,
    *,
    supplier_id: str = "SUP-001",
    invoice_date: str = "2026-05-01",
    gross_amount: str = "1000.0000",
    currency: str = "USD",
    invoice_type: str = "STANDARD",
    invoice_status: str = "POSTED",
    eligibility_reason: str = "ELIGIBLE",
    payment_count: int = 0,
    settled_payment_count: int = 0,
    po_record_key: str | None = "PO-001",
    po_matching_basis: str | None = "SINGLE_INVOICE",
    po_status: str | None = "APPROVED",
) -> JsonMapping:
    """Build one exact common population row with stored four-place arithmetic."""
    gross = Decimal(gross_amount)
    tax = (gross / Decimal("10")).quantize(Decimal("0.0001"))
    net = gross - tax
    return {
        "invoice_record_key": key,
        "tenant_id": "TENANT-DEMO",
        "supplier_id": supplier_id,
        "legal_entity_id": "LE-US-01",
        "business_unit_id": "BU-US-OPS",
        "invoice_type": invoice_type,
        "invoice_date": invoice_date,
        "posting_date": invoice_date,
        "due_date": "2026-05-31",
        "net_amount": format(net, "f"),
        "tax_amount": format(tax, "f"),
        "gross_amount": gross_amount,
        "currency": currency,
        "invoice_status": invoice_status,
        "po_record_key": po_record_key,
        "po_matching_basis": po_matching_basis,
        "po_status": po_status,
        "payment_count": payment_count,
        "settled_payment_count": settled_payment_count,
        "eligibility_reason": eligibility_reason,
    }


def duplicate_row(
    common: JsonMapping,
    *,
    normalized_invoice_number: str | None = "DUP001",
) -> JsonMapping:
    """Project one common row into the duplicate-candidate contract."""
    return {
        "invoice_record_key": common["invoice_record_key"],
        "tenant_id": common["tenant_id"],
        "supplier_id": common["supplier_id"],
        "legal_entity_id": common["legal_entity_id"],
        "business_unit_id": common["business_unit_id"],
        "normalized_invoice_number": normalized_invoice_number,
        "invoice_date": common["invoice_date"],
        "gross_amount": common["gross_amount"],
        "currency": common["currency"],
        "invoice_type": common["invoice_type"],
        "invoice_status": common["invoice_status"],
    }


def po_row(
    common: JsonMapping,
    *,
    po_record_key: str | None = "PO-001",
    approved_amount: str | None = "1000.0000",
    po_currency: str | None = "USD",
    matching_basis: str | None = "SINGLE_INVOICE",
    no_po_exception_ref: str | None = None,
    no_po_exception_approved: bool = False,
) -> JsonMapping:
    """Project one common row into the PO/missing-PO contract."""
    has_po = po_record_key is not None
    return {
        "invoice_record_key": common["invoice_record_key"],
        "tenant_id": common["tenant_id"],
        "supplier_id": common["supplier_id"],
        "legal_entity_id": common["legal_entity_id"],
        "business_unit_id": common["business_unit_id"],
        "po_record_key": po_record_key,
        "po_tenant_id": common["tenant_id"] if has_po else None,
        "invoice_type": common["invoice_type"],
        "invoice_status": common["invoice_status"],
        "invoice_gross_amount": common["gross_amount"],
        "invoice_currency": common["currency"],
        "po_approved_amount": approved_amount if has_po else None,
        "po_currency": po_currency if has_po else None,
        "po_matching_basis": matching_basis if has_po else None,
        "po_status": "APPROVED" if has_po else None,
        "po_supplier_id": common["supplier_id"] if has_po else None,
        "po_legal_entity_id": common["legal_entity_id"] if has_po else None,
        "po_business_unit_id": common["business_unit_id"] if has_po else None,
        "no_po_exception_ref": no_po_exception_ref,
        "no_po_exception_approved": no_po_exception_approved,
    }


def payment_terms_row(
    common: JsonMapping,
    *,
    payment_date: str | None = "2026-05-31",
    payment_count: int = 1,
    settled_payment_count: int = 1,
    payment_currency: str | None = "USD",
) -> JsonMapping:
    """Project one common row into the payment-term contract."""
    has_payment = payment_count > 0
    return {
        "invoice_record_key": common["invoice_record_key"],
        "tenant_id": common["tenant_id"],
        "supplier_id": common["supplier_id"],
        "legal_entity_id": common["legal_entity_id"],
        "business_unit_id": common["business_unit_id"],
        "invoice_type": common["invoice_type"],
        "invoice_status": common["invoice_status"],
        "invoice_date": common["invoice_date"],
        "due_date": common["due_date"],
        "payment_terms_days": 30,
        "invoice_currency": common["currency"],
        "payment_count": payment_count,
        "settled_payment_count": settled_payment_count,
        "payment_date": payment_date if has_payment else None,
        "payment_currency": payment_currency if has_payment else None,
        "payment_status": "SETTLED" if has_payment else None,
        "payment_tenant_id": common["tenant_id"] if has_payment else None,
        "payment_invoice_record_key": common["invoice_record_key"] if has_payment else None,
        "payment_legal_entity_id": common["legal_entity_id"] if has_payment else None,
        "payment_business_unit_id": common["business_unit_id"] if has_payment else None,
    }


def payment_amount_row(
    common: JsonMapping,
    *,
    payment_amount: str | None = "1000.0000",
    payment_count: int = 1,
    settled_payment_count: int = 1,
    payment_currency: str | None = "USD",
) -> JsonMapping:
    """Project one common row into the payment-amount contract."""
    terms = payment_terms_row(
        common,
        payment_count=payment_count,
        settled_payment_count=settled_payment_count,
        payment_currency=payment_currency,
    )
    terms.pop("invoice_date")
    terms.pop("due_date")
    terms.pop("payment_terms_days")
    terms["invoice_gross_amount"] = common["gross_amount"]
    terms["payment_amount"] = payment_amount if payment_count > 0 else None
    return terms


def evidence_ledger() -> InMemoryEvidenceLedger:
    """Create a deterministic in-memory ledger with enough Stage 5 capacity."""
    return InMemoryEvidenceLedger(
        id_factory=lambda: f"E-AP-CALC-{next(_ID_SEQUENCE):05d}",
        clock=lambda: FIXED_TIME,
        max_items_per_task=500,
    )


def add_policy_evidence(
    ledger: InMemoryEvidenceLedger,
    *,
    task_id: str = "T-AP-AN-001",
) -> tuple[dict[str, JsonValue], dict[str, str]]:
    """Add exact controlled Document Evidence for every manifest rule."""
    bundle = load_ap_policy_bundle(AP_POLICY_ROOT, expected_tenant_id="TENANT-DEMO")
    mapping: dict[str, str] = {}
    for index, rule in enumerate(bundle.rule_manifest.rules, start=1):
        evidence_id = f"E-AP-DOC-{index:02d}"
        binding = rule.binding
        ledger.add(
            EvidenceItem(
                evidence_id=evidence_id,
                task_id=task_id,
                step_id="S-AP-KNOWLEDGE",
                tool_call_id="TC-AP-KNOWLEDGE",
                source_type=EvidenceType.DOCUMENT,
                source_reference=EvidenceSourceReference(
                    reference=JsonObject(
                        {
                            "document_id": binding.document_id,
                            "document_version": binding.document_version,
                            "chunk_id": binding.chunk_id,
                            "page": binding.page,
                            "document_checksum": binding.document_checksum,
                            "excerpt_checksum": binding.excerpt_checksum,
                            "policy_rule_set_version": bundle.rule_manifest.rule_set_version,
                            "bound_rule_ids": [rule.rule_id],
                        }
                    )
                ),
                content=EvidenceContent(
                    data=JsonObject({"excerpt": "Controlled synthetic AP policy excerpt"}),
                    classification="CONFIDENTIAL",
                    checksum=binding.excerpt_checksum,
                ),
                timestamp=FIXED_TIME,
            ),
            tenant_id="TENANT-DEMO",
        )
        mapping[rule.rule_id] = evidence_id
    snapshot: dict[str, JsonValue] = {
        "rule_manifest": cast(JsonValue, bundle.rule_manifest.model_dump(mode="json")),
        "document_evidence": cast(
            JsonValue,
            [
                {"rule_id": rule.rule_id, "evidence_id": mapping[rule.rule_id]}
                for rule in bundle.rule_manifest.rules
            ],
        ),
    }
    return snapshot, mapping


def add_database_dataset(
    ledger: InMemoryEvidenceLedger,
    template: APDatabaseTemplate,
    rows: list[JsonMapping],
    *,
    task_id: str = "T-AP-AN-001",
    truncated: bool = False,
) -> dict[str, JsonValue]:
    """Add one exact minimized DATABASE Evidence item and return a DatasetReference payload."""
    evidence_id = f"E-AP-DB-{template.value}"
    dataset_checksum = checksum(rows)
    scope: JsonMapping = {
        "tenant_scope_hash": checksum("TENANT-DEMO"),
        "time_scope_hash": checksum({"start_date": "2026-04-01", "end_date": "2026-06-30"}),
        "supplier_count": 0,
        "supplier_scope_hash": checksum([]),
        "legal_entity_count": 1,
        "legal_entity_scope_hash": checksum(["LE-US-01"]),
        "business_unit_count": 0,
        "business_unit_scope_hash": checksum([]),
        "currency_count": 0,
        "currency_scope_hash": checksum([]),
    }
    ledger.add(
        EvidenceItem(
            evidence_id=evidence_id,
            task_id=task_id,
            step_id=f"S-{template.value}",
            tool_call_id=f"TC-{template.value}",
            source_type=EvidenceType.DATABASE,
            source_reference=EvidenceSourceReference(
                reference=JsonObject(
                    {
                        "query_template_id": template.value,
                        "template_version": template.value,
                        "schema_version": "accounts_payable.v1",
                        "schema_snapshot": {
                            "version": "accounts_payable.v1",
                            "snapshot_at": "2026-10-01T00:00:00+00:00",
                        },
                        "query_fingerprint": checksum({"template": template.value, "scope": scope}),
                        "table_names": ["invoices"],
                        "column_names": ["invoice_record_key"],
                        "statement_type": "SELECT",
                        "read_only": True,
                        "snapshot_at": "2026-10-01T00:00:00+00:00",
                        "parameter_summary": scope,
                        "row_count": len(rows),
                        "dataset_checksum": dataset_checksum,
                    }
                )
            ),
            content=EvidenceContent(
                data=JsonObject(
                    {
                        "row_count": len(rows),
                        "empty_result": not rows,
                        "truncated": truncated,
                    }
                ),
                classification="CONFIDENTIAL",
                checksum=dataset_checksum,
            ),
            timestamp=FIXED_TIME,
        ),
        tenant_id="TENANT-DEMO",
    )
    return {
        "template_id": template.value,
        "template_version": template.value,
        "evidence_id": evidence_id,
        "dataset_checksum": dataset_checksum,
        "rows": cast(JsonValue, rows),
    }


def detection_arguments(
    operation: APAnalyticsOperation,
    population_dataset: dict[str, JsonValue],
    dedicated_dataset: dict[str, JsonValue],
    rule_snapshot: dict[str, JsonValue],
    *,
    requested_materiality: list[dict[str, str]] | None = None,
    effective_materiality: list[dict[str, str]] | None = None,
) -> JsonObject:
    """Build one strict detection-union request."""
    effective = effective_materiality or [
        {"currency": "CNY", "amount": "5000.0000"},
        {"currency": "USD", "amount": "1000.0000"},
    ]
    return JsonObject(
        {
            "operation_name": operation.value,
            "operation_version": "1.0.0",
            "datasets": cast(JsonValue, [population_dataset, dedicated_dataset]),
            "rule_snapshot": cast(JsonValue, rule_snapshot),
            "parameters": {
                "requested_materiality": cast(JsonValue, requested_materiality or []),
                "effective_materiality": cast(JsonValue, effective),
            },
            "engine_version": "accounts_payable_analytics.v1",
        }
    )


def aggregation_arguments(
    operation: APAnalyticsOperation,
    population_dataset: dict[str, JsonValue],
    rule_snapshot: dict[str, JsonValue],
    calculation_evidence_ids: tuple[str, ...],
) -> JsonObject:
    """Build one strict summary or supplier-rate request."""
    return JsonObject(
        {
            "operation_name": operation.value,
            "operation_version": "1.0.0",
            "datasets": cast(JsonValue, [population_dataset]),
            "rule_snapshot": cast(JsonValue, rule_snapshot),
            "parameters": {"calculation_evidence_ids": list(calculation_evidence_ids)},
            "engine_version": "accounts_payable_analytics.v1",
        }
    )


def analytics_context(
    arguments: JsonObject,
    *,
    task_id: str = "T-AP-AN-001",
    call_id: str = "TC-AP-AN-001",
    tenant_id: str = "TENANT-DEMO",
    purpose: str = "accounts_payable_analysis.v1",
) -> ToolExecutionContext:
    """Bind one AP request to trusted finance execution context."""
    call = ToolCall(
        tool_call_id=call_id,
        task_id=task_id,
        step_id=f"S-{call_id}",
        tool_name="analysis_engine",
        tool_version=AccountsPayableAnalyticsTool.definition.tool_version,
        input=arguments,
        idempotency_key=f"IDEMPOTENCY-{call_id}",
        approval_id=None,
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        tenant_id=tenant_id,
        user_id="U-FINANCE-001",
    )
    return ToolExecutionContext(
        call=call,
        tenant_id=tenant_id,
        user_id="U-FINANCE-001",
        roles=("finance_analyst",),
        scopes=("finance:ap.detail", "tool.execute"),
        purpose=purpose,
    )


__all__ = [
    "AP_POLICY_ROOT",
    "FIXED_TIME",
    "add_database_dataset",
    "add_policy_evidence",
    "aggregation_arguments",
    "analytics_context",
    "checksum",
    "detection_arguments",
    "duplicate_row",
    "evidence_ledger",
    "payment_amount_row",
    "payment_terms_row",
    "po_row",
    "population_row",
]
