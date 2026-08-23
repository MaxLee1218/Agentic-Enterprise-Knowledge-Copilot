"""Strong contracts for the frozen Accounts Payable report profile."""

from __future__ import annotations

import math
from datetime import date, datetime
from enum import StrEnum
from typing import Literal, cast

from pydantic import Field, field_validator, model_validator

from copilot.contracts import (
    APReportClaimV1,
    ContractModel,
    EvidenceType,
    JsonObject,
    ReportLanguage,
)
from copilot.contracts.base import JsonMapping
from copilot.contracts.validators import validate_identifier, validate_utc_datetime
from copilot.tools.analytics.ap_schemas import (
    APAnalyticsOperation,
    APAnalyticsResultV1,
    APExceptionStatus,
    APPolicyRuleSnapshotV1,
)
from copilot.tools.reporting.schemas import ReportFormat

AP_REPORT_SCHEMA_VERSION: Literal["accounts_payable_report_model.v1"] = (
    "accounts_payable_report_model.v1"
)
AP_REPORT_TEMPLATE_VERSION: Literal["accounts_payable_report.v1"] = "accounts_payable_report.v1"
AP_REPORT_GENERATOR_VERSION: Literal["report_generator.v2"] = "report_generator.v2"
AP_REPORT_TOOL_VERSION = "2.0.0"
AP_JSON_MAX_SIZE_BYTES = 25 * 1024 * 1024
AP_PDF_MAX_SIZE_BYTES = 15 * 1024 * 1024
AP_MATERIAL_DETAIL_LIMIT = 100


class APDetailAccess(StrEnum):
    """Trusted report disclosure mode; it is never model-selected."""

    AGGREGATE = "AGGREGATE"
    DETAIL = "DETAIL"


class APReportScopeV1(ContractModel):
    """Exact authorized AP dimensions reproduced in the report."""

    start_date: date
    end_date: date
    supplier_ids: tuple[str, ...] = Field(max_length=100)
    legal_entity_ids: tuple[str, ...] = Field(min_length=1, max_length=10)
    business_unit_ids: tuple[str, ...] = Field(max_length=50)
    currency_scope: tuple[str, ...]

    _validate_ids = field_validator("supplier_ids", "legal_entity_ids", "business_unit_ids")(
        lambda values: tuple(validate_identifier(value) for value in values)
    )

    @field_validator("currency_scope")
    @classmethod
    def validate_currencies(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip().upper() for value in values)
        if any(len(value) != 3 or not value.isalpha() for value in normalized):
            raise ValueError("AP report currencies must be uppercase three-letter codes")
        if len(set(normalized)) != len(normalized):
            raise ValueError("AP report currencies must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_scope(self) -> APReportScopeV1:
        if self.start_date > self.end_date:
            raise ValueError("AP report start_date must not be after end_date")
        if (self.end_date - self.start_date).days + 1 > 366:
            raise ValueError("AP report range cannot exceed 366 inclusive days")
        for name in ("supplier_ids", "legal_entity_ids", "business_unit_ids"):
            values = getattr(self, name)
            if len(set(values)) != len(values):
                raise ValueError(f"AP report {name} must be unique")
        return self


class APReportRequestV1(ContractModel):
    """Exact frozen input accepted by the AP report-generator profile."""

    task_id: str
    scope: APReportScopeV1
    exception_summary_result: APAnalyticsResultV1
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=250)
    policy_rule_snapshot: APPolicyRuleSnapshotV1
    template_version: Literal["accounts_payable_report.v1"]
    format: ReportFormat
    language: ReportLanguage
    detail_access: APDetailAccess

    _validate_task_id = field_validator("task_id")(validate_identifier)
    _validate_evidence_ids = field_validator("evidence_refs")(
        lambda values: tuple(validate_identifier(value) for value in values)
    )

    @model_validator(mode="after")
    def validate_request(self) -> APReportRequestV1:
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("AP report evidence_refs must be unique")
        if (
            self.exception_summary_result.operation_name
            is not APAnalyticsOperation.EXCEPTION_SUMMARY
        ):
            raise ValueError("AP report requires the frozen exception-summary result")
        manifest = self.policy_rule_snapshot.rule_manifest
        if (
            self.exception_summary_result.rule_set_version != manifest.rule_set_version
            or self.exception_summary_result.manifest_checksum != manifest.manifest_checksum
        ):
            raise ValueError("AP report summary and policy snapshot versions differ")
        return self


class APReportTaskSummaryV1(ContractModel):
    """Report identity and pre-verification lifecycle context."""

    task_id: str
    task_status: Literal["EXECUTING"]

    _validate_task_id = field_validator("task_id")(validate_identifier)


class APPolicyReferenceV1(ContractModel):
    """One exact controlled policy/rule citation."""

    evidence_id: str
    document_id: str
    document_version: str
    location: str
    excerpt: str
    classification: str
    rule_ids: tuple[str, ...] = Field(min_length=1)

    _validate_ids = field_validator("evidence_id", "document_id", "document_version")(
        validate_identifier
    )


class APDataSourceV1(ContractModel):
    """Minimized database coverage record without raw financial rows or SQL."""

    evidence_id: str
    query_template_id: str
    query_fingerprint: str
    row_count: int = Field(ge=0, le=50_000)
    empty_result: bool
    truncated: Literal[False]
    snapshot_at: datetime
    dataset_checksum: str

    _validate_ids = field_validator("evidence_id", "query_template_id", "query_fingerprint")(
        validate_identifier
    )
    _validate_snapshot = field_validator("snapshot_at")(validate_utc_datetime)

    @model_validator(mode="after")
    def validate_empty_state(self) -> APDataSourceV1:
        if self.empty_result != (self.row_count == 0):
            raise ValueError("AP data source empty_result differs from row_count")
        return self


class APDataOverviewV1(ContractModel):
    """Coverage copied from governed Database Evidence metadata."""

    sources: tuple[APDataSourceV1, ...] = Field(min_length=1)
    empty_result: bool

    @model_validator(mode="after")
    def validate_empty_state(self) -> APDataOverviewV1:
        if self.empty_result != all(source.empty_result for source in self.sources):
            raise ValueError("AP data overview empty state differs from its sources")
        return self


class APExceptionSummarySectionV1(ContractModel):
    """Canonical summary values copied directly from deterministic Analytics."""

    operation_name: Literal[APAnalyticsOperation.EXCEPTION_SUMMARY]
    metrics: JsonObject
    eligibility_count: int = Field(ge=0, le=50_000)
    exclusion_count: int = Field(ge=0, le=50_000)
    exclusion_count_by_reason: dict[str, int]
    finding_count: int = Field(ge=0, le=5_000)
    warning_count: int = Field(ge=0, le=5_000)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    empty_result: bool


class APFindingV1(ContractModel):
    """One safe exception detail copied from Calculation Evidence."""

    exception_id: str
    exception_type: str
    supplier_id: str
    currency: str
    status: APExceptionStatus
    invoice_record_key: str | None = None
    observed_values: JsonObject
    threshold_values: JsonObject
    rule_id: str
    rule_version: str
    reason_codes: tuple[str, ...]
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    _validate_ids = field_validator("exception_id", "supplier_id", "rule_id", "rule_version")(
        validate_identifier
    )

    @model_validator(mode="after")
    def validate_detail_identity(self) -> APFindingV1:
        if self.invoice_record_key is not None:
            validate_identifier(self.invoice_record_key)
        return self


class APSupplierSummaryV1(ContractModel):
    """One existing supplier-rate result; no report-time rate calculation occurs."""

    supplier_id: str
    exception_invoice_count: int = Field(ge=0)
    eligible_invoice_count: int = Field(ge=0)
    supplier_exception_rate: str | None
    invoice_amount_by_currency: JsonObject
    exception_amount_by_currency: JsonObject
    exclusion_count: int = Field(ge=0)
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    _validate_supplier_id = field_validator("supplier_id")(validate_identifier)


class APRiskObservationV1(ContractModel):
    """Bounded review observation, never a supplier-risk score."""

    observation_id: str
    statement: str = Field(min_length=1)
    level: Literal["INFORMATIONAL", "REVIEW_REQUIRED"]
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    _validate_id = field_validator("observation_id")(validate_identifier)


class APRecommendationV1(ContractModel):
    """Internal manual recommendation without transaction authority."""

    action_id: str
    action: str = Field(min_length=1)
    evidence_ids: tuple[str, ...]

    _validate_id = field_validator("action_id")(validate_identifier)


class APLimitationV1(ContractModel):
    """One explicit interpretation or coverage boundary."""

    code: str
    statement: str = Field(min_length=1)

    _validate_code = field_validator("code")(validate_identifier)


class APEvidenceReferenceV1(ContractModel):
    """Minimal current-task Evidence index used by report citations."""

    evidence_id: str
    source_type: EvidenceType
    source_step_id: str
    source_tool_call_id: str
    checksum: str
    input_evidence_ids: tuple[str, ...]

    _validate_ids = field_validator("evidence_id", "source_step_id", "source_tool_call_id")(
        validate_identifier
    )


class APEvidenceSectionV1(ContractModel):
    """Explicit machine-readable claim envelope and Evidence index."""

    claims: tuple[APReportClaimV1, ...] = Field(min_length=1)
    references: tuple[APEvidenceReferenceV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_values(self) -> APEvidenceSectionV1:
        claim_ids = tuple(item.claim_id for item in self.claims)
        evidence_ids = tuple(item.evidence_id for item in self.references)
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("AP report claim identifiers must be unique")
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("AP report Evidence references must be unique")
        return self


class APExecutionStepV1(ContractModel):
    """Evidence-producing execution entry without business payloads."""

    step_id: str
    tool_call_id: str
    evidence_id: str
    source_type: EvidenceType

    _validate_ids = field_validator("step_id", "tool_call_id", "evidence_id")(validate_identifier)


class APExecutionMetadataV1(ContractModel):
    """Version and render provenance frozen into both output formats."""

    generated_at: datetime
    schema_version: Literal["accounts_payable_report_model.v1"]
    template_version: Literal["accounts_payable_report.v1"]
    generator_version: Literal["report_generator.v2"]
    rule_set_version: Literal["ap_rules.2026.1"]
    policy_manifest_checksum: str
    language: ReportLanguage
    detail_access: APDetailAccess
    verification_status: Literal["PENDING"]

    _validate_generated_at = field_validator("generated_at")(validate_utc_datetime)


class AccountsPayableReportV1(ContractModel):
    """Single canonical source model shared by AP JSON and PDF renderers."""

    title: str = Field(min_length=1)
    executive_summary: str = Field(min_length=1)
    task_summary: APReportTaskSummaryV1
    scope: APReportScopeV1
    data_overview: APDataOverviewV1
    applicable_policies: tuple[APPolicyReferenceV1, ...] = Field(min_length=1)
    exception_summary: APExceptionSummarySectionV1
    duplicate_invoice_findings: tuple[APFindingV1, ...]
    po_compliance_findings: tuple[APFindingV1, ...]
    payment_findings: tuple[APFindingV1, ...]
    material_exceptions: tuple[APFindingV1, ...] = Field(max_length=AP_MATERIAL_DETAIL_LIMIT)
    supplier_summary: tuple[APSupplierSummaryV1, ...]
    risk_observations: tuple[APRiskObservationV1, ...]
    recommended_actions: tuple[APRecommendationV1, ...]
    limitations: tuple[APLimitationV1, ...] = Field(min_length=1)
    evidence: APEvidenceSectionV1
    execution_trace: tuple[APExecutionStepV1, ...] = Field(min_length=1)
    execution_metadata: APExecutionMetadataV1

    @model_validator(mode="after")
    def validate_document(self) -> AccountsPayableReportV1:
        if self.task_summary.task_id == "":
            raise ValueError("AP report task identity is required")
        if self.execution_metadata.detail_access is APDetailAccess.AGGREGATE:
            sections = (
                self.duplicate_invoice_findings,
                self.po_compliance_findings,
                self.payment_findings,
                self.material_exceptions,
            )
            if any(sections):
                raise ValueError("Aggregate AP reports cannot expose record-level details")
        _reject_non_finite(self.model_dump(mode="python", exclude={"execution_metadata"}))
        return self


def _reject_non_finite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("AP report values must not contain NaN or Infinity")
    if isinstance(value, dict):
        for child in value.values():
            _reject_non_finite(child)
    elif isinstance(value, list | tuple):
        for child in value:
            _reject_non_finite(child)


AP_REPORT_INPUT_SCHEMA = cast(JsonMapping, APReportRequestV1.model_json_schema())
AP_REPORT_OUTPUT_SCHEMA: JsonMapping = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "artifact_id",
        "type",
        "location",
        "created_at",
        "checksum",
        "size_bytes",
        "citation_map",
        "generator_version",
    ],
    "properties": {
        "artifact_id": {"type": "string"},
        "type": {
            "type": "string",
            "enum": ["ACCOUNTS_PAYABLE_REPORT_PDF", "ACCOUNTS_PAYABLE_REPORT_JSON"],
        },
        "location": {"type": "string"},
        "created_at": {"type": "string", "format": "date-time"},
        "checksum": {"type": "string"},
        "size_bytes": {"type": "integer", "minimum": 1},
        "citation_map": {"type": "object"},
        "generator_version": {"type": "string"},
    },
}


__all__ = [
    "APDetailAccess",
    "APEvidenceReferenceV1",
    "APEvidenceSectionV1",
    "APExecutionMetadataV1",
    "APExecutionStepV1",
    "APFindingV1",
    "AP_JSON_MAX_SIZE_BYTES",
    "AP_MATERIAL_DETAIL_LIMIT",
    "AP_PDF_MAX_SIZE_BYTES",
    "AP_REPORT_GENERATOR_VERSION",
    "AP_REPORT_INPUT_SCHEMA",
    "AP_REPORT_OUTPUT_SCHEMA",
    "AP_REPORT_SCHEMA_VERSION",
    "AP_REPORT_TEMPLATE_VERSION",
    "AP_REPORT_TOOL_VERSION",
    "APReportRequestV1",
    "APReportScopeV1",
    "APSupplierSummaryV1",
    "AccountsPayableReportV1",
]
