"""Typed contracts for the frozen Accounts Payable analytics v1 profile."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Final, Literal, TypeAlias, cast

from pydantic import Field, TypeAdapter, field_validator, model_validator

from copilot.contracts import (
    APExceptionType,
    APPolicyRuleManifestV1,
    ContractModel,
    CurrencyAmountV1,
    JsonObject,
)
from copilot.contracts.base import JsonMapping

AP_ANALYTICS_CONTRACT_PROFILE = "accounts_payable_analytics.v1"
AP_ANALYTICS_ENGINE_VERSION: Final[Literal["accounts_payable_analytics.v1"]] = (
    "accounts_payable_analytics.v1"
)
AP_ANALYTICS_OPERATION_VERSION: Final[Literal["1.0.0"]] = "1.0.0"
AP_ANALYTICS_TOOL_VERSION = "2.0.0-deterministic"
AP_MAX_SOURCE_ROWS = 50_000
AP_MAX_EXCEPTION_RECORDS = 5_000
AP_MAX_SUPPLIERS = 100
AP_CALCULATION_BATCH_SIZE = 1_000
INVOICE_NUMBER_NORMALIZATION_VERSION: Final[Literal["invoice_number_normalization.v1"]] = (
    "invoice_number_normalization.v1"
)

_CHECKSUM = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class APAnalyticsOperation(StrEnum):
    """The seven exact operation identifiers authorized for AP v1."""

    EXACT_DUPLICATE_INVOICE_DETECTION = "ap.exact_duplicate_invoice_detection.v1"
    INVOICE_PO_VARIANCE_DETECTION = "ap.invoice_po_variance_detection.v1"
    MISSING_PO_DETECTION = "ap.missing_po_detection.v1"
    PAYMENT_TERM_COMPLIANCE_DETECTION = "ap.payment_term_compliance_detection.v1"
    OVERPAYMENT_DETECTION = "ap.overpayment_detection.v1"
    EXCEPTION_SUMMARY = "ap.exception_summary.v1"
    SUPPLIER_EXCEPTION_RATE = "ap.supplier_exception_rate.v1"


class APDatabaseTemplate(StrEnum):
    """The five read models exposed by the frozen AP database profile."""

    INVOICE_POPULATION = "ap_invoice_population_v1"
    DUPLICATE_CANDIDATES = "ap_duplicate_invoice_candidates_v1"
    INVOICE_PO_VARIANCE = "ap_invoice_po_variance_v1"
    PAYMENT_TERMS = "ap_payment_terms_v1"
    PAYMENT_AMOUNT = "ap_payment_amount_v1"


class APExceptionStatus(StrEnum):
    """Presentation severity applied only after deterministic detection."""

    WARNING = "WARNING"
    FINDING = "FINDING"


Money: TypeAlias = Annotated[Decimal, Field(max_digits=20, decimal_places=4)]
NonNegativeMoney: TypeAlias = Annotated[
    Decimal,
    Field(ge=Decimal("0"), max_digits=20, decimal_places=4),
]
CanonicalRatio: TypeAlias = Annotated[
    Decimal,
    Field(ge=Decimal("0"), le=Decimal("1"), max_digits=12, decimal_places=8),
]


def _identifier(value: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError("identifier must not be blank")
    return clean


def _optional_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    return clean or None


def _currency(value: str) -> str:
    clean = value.strip().upper()
    if _CURRENCY.fullmatch(clean) is None:
        raise ValueError("currency must be an uppercase three-letter code")
    return clean


def _optional_currency(value: str | None) -> str | None:
    return None if value is None else _currency(value)


class APInvoicePopulationRowV1(ContractModel):
    """One common-population row produced by ``ap_invoice_population_v1``."""

    invoice_record_key: str
    tenant_id: str
    supplier_id: str
    legal_entity_id: str
    business_unit_id: str
    invoice_type: str
    invoice_date: date
    posting_date: date
    due_date: date
    net_amount: NonNegativeMoney
    tax_amount: NonNegativeMoney
    gross_amount: NonNegativeMoney
    currency: str
    invoice_status: str
    po_record_key: str | None = None
    po_matching_basis: str | None = None
    po_status: str | None = None
    payment_count: int = Field(ge=0)
    settled_payment_count: int = Field(ge=0)
    eligibility_reason: str

    _ids = field_validator(
        "invoice_record_key",
        "tenant_id",
        "supplier_id",
        "legal_entity_id",
        "business_unit_id",
        "invoice_type",
        "invoice_status",
        "eligibility_reason",
    )(_identifier)
    _optional_ids = field_validator("po_record_key", "po_matching_basis", "po_status")(
        _optional_identifier
    )
    _currency = field_validator("currency")(_currency)


class APDuplicateInvoiceRowV1(ContractModel):
    """One exact-duplicate candidate row; nullable key fields become exclusions."""

    invoice_record_key: str
    tenant_id: str
    supplier_id: str
    legal_entity_id: str
    business_unit_id: str
    normalized_invoice_number: str | None
    invoice_date: date | None
    gross_amount: Money | None
    currency: str | None
    invoice_type: str
    invoice_status: str

    _ids = field_validator(
        "invoice_record_key",
        "tenant_id",
        "supplier_id",
        "legal_entity_id",
        "business_unit_id",
        "invoice_type",
        "invoice_status",
    )(_identifier)
    _normalized_number = field_validator("normalized_invoice_number")(_optional_identifier)
    _currency = field_validator("currency")(_optional_currency)


class APInvoicePOVarianceRowV1(ContractModel):
    """One invoice/PO comparison or missing-PO fact row."""

    invoice_record_key: str
    tenant_id: str
    supplier_id: str
    legal_entity_id: str
    business_unit_id: str
    po_record_key: str | None = None
    po_tenant_id: str | None = None
    invoice_type: str
    invoice_status: str
    invoice_gross_amount: Money
    invoice_currency: str
    po_approved_amount: Money | None = None
    po_currency: str | None = None
    po_matching_basis: str | None = None
    po_status: str | None = None
    po_supplier_id: str | None = None
    po_legal_entity_id: str | None = None
    po_business_unit_id: str | None = None
    no_po_exception_ref: str | None = None
    no_po_exception_approved: bool

    _ids = field_validator(
        "invoice_record_key",
        "tenant_id",
        "supplier_id",
        "legal_entity_id",
        "business_unit_id",
        "invoice_type",
        "invoice_status",
    )(_identifier)
    _optional_ids = field_validator(
        "po_record_key",
        "po_tenant_id",
        "po_matching_basis",
        "po_status",
        "po_supplier_id",
        "po_legal_entity_id",
        "po_business_unit_id",
        "no_po_exception_ref",
    )(_optional_identifier)
    _invoice_currency = field_validator("invoice_currency")(_currency)
    _po_currency = field_validator("po_currency")(_optional_currency)


class APPaymentTermsRowV1(ContractModel):
    """One due-date and settlement-shape row."""

    invoice_record_key: str
    tenant_id: str
    supplier_id: str
    legal_entity_id: str
    business_unit_id: str
    invoice_type: str
    invoice_status: str
    invoice_date: date
    due_date: date
    payment_terms_days: int = Field(ge=0, le=365)
    invoice_currency: str
    payment_count: int = Field(ge=0)
    settled_payment_count: int = Field(ge=0)
    payment_date: date | None = None
    payment_currency: str | None = None
    payment_status: str | None = None
    payment_tenant_id: str | None = None
    payment_invoice_record_key: str | None = None
    payment_legal_entity_id: str | None = None
    payment_business_unit_id: str | None = None

    _ids = field_validator(
        "invoice_record_key",
        "tenant_id",
        "supplier_id",
        "legal_entity_id",
        "business_unit_id",
        "invoice_type",
        "invoice_status",
    )(_identifier)
    _optional_ids = field_validator(
        "payment_status",
        "payment_tenant_id",
        "payment_invoice_record_key",
        "payment_legal_entity_id",
        "payment_business_unit_id",
    )(_optional_identifier)
    _invoice_currency = field_validator("invoice_currency")(_currency)
    _payment_currency = field_validator("payment_currency")(_optional_currency)


class APPaymentAmountRowV1(ContractModel):
    """One invoice/payment amount and settlement-shape row."""

    invoice_record_key: str
    tenant_id: str
    supplier_id: str
    legal_entity_id: str
    business_unit_id: str
    invoice_type: str
    invoice_status: str
    invoice_gross_amount: Money
    invoice_currency: str
    payment_count: int = Field(ge=0)
    settled_payment_count: int = Field(ge=0)
    payment_date: date | None = None
    payment_amount: Money | None = None
    payment_currency: str | None = None
    payment_status: str | None = None
    payment_tenant_id: str | None = None
    payment_invoice_record_key: str | None = None
    payment_legal_entity_id: str | None = None
    payment_business_unit_id: str | None = None

    _ids = field_validator(
        "invoice_record_key",
        "tenant_id",
        "supplier_id",
        "legal_entity_id",
        "business_unit_id",
        "invoice_type",
        "invoice_status",
    )(_identifier)
    _optional_ids = field_validator(
        "payment_status",
        "payment_tenant_id",
        "payment_invoice_record_key",
        "payment_legal_entity_id",
        "payment_business_unit_id",
    )(_optional_identifier)
    _invoice_currency = field_validator("invoice_currency")(_currency)
    _payment_currency = field_validator("payment_currency")(_optional_currency)


APSourceRowV1: TypeAlias = (
    APInvoicePopulationRowV1
    | APDuplicateInvoiceRowV1
    | APInvoicePOVarianceRowV1
    | APPaymentTermsRowV1
    | APPaymentAmountRowV1
)


class APDatasetReferenceV1(ContractModel):
    """Rows bound to one current-task AP DATABASE Evidence item and checksum."""

    template_id: APDatabaseTemplate
    template_version: str
    evidence_id: str
    dataset_checksum: str
    rows: tuple[JsonObject, ...] = Field(max_length=AP_MAX_SOURCE_ROWS)

    _evidence_id = field_validator("evidence_id")(_identifier)

    @field_validator("dataset_checksum")
    @classmethod
    def validate_checksum(cls, value: str) -> str:
        clean = value.strip()
        if _CHECKSUM.fullmatch(clean) is None:
            raise ValueError("dataset checksum must be SHA-256")
        return clean

    @model_validator(mode="after")
    def validate_template_version(self) -> APDatasetReferenceV1:
        if self.template_version != self.template_id.value:
            raise ValueError("AP dataset template version must equal its frozen template ID")
        return self


class APRuleEvidenceReferenceV1(ContractModel):
    """Bind one executable rule to its exact current-task DOCUMENT Evidence."""

    rule_id: str
    evidence_id: str

    _ids = field_validator("rule_id", "evidence_id")(_identifier)


class APPolicyRuleSnapshotV1(ContractModel):
    """Complete validated rule manifest and its exact document-evidence bindings."""

    rule_manifest: APPolicyRuleManifestV1
    document_evidence: tuple[APRuleEvidenceReferenceV1, ...]

    @model_validator(mode="after")
    def validate_rule_evidence_coverage(self) -> APPolicyRuleSnapshotV1:
        rule_ids = tuple(item.rule_id for item in self.rule_manifest.rules)
        bound_ids = tuple(item.rule_id for item in self.document_evidence)
        evidence_ids = tuple(item.evidence_id for item in self.document_evidence)
        if len(set(bound_ids)) != len(bound_ids) or set(bound_ids) != set(rule_ids):
            raise ValueError("rule snapshot must bind every manifest rule exactly once")
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("rule snapshot document evidence IDs must be unique")
        return self


class APDetectionParametersV1(ContractModel):
    """Materiality inputs frozen by the Task Contract and revalidated by Analytics."""

    requested_materiality: tuple[CurrencyAmountV1, ...] = ()
    effective_materiality: tuple[CurrencyAmountV1, ...]

    @model_validator(mode="after")
    def validate_unique_currencies(self) -> APDetectionParametersV1:
        for name, values in (
            ("requested_materiality", self.requested_materiality),
            ("effective_materiality", self.effective_materiality),
        ):
            currencies = tuple(item.currency for item in values)
            if len(set(currencies)) != len(currencies):
                raise ValueError(f"{name} currencies must be unique")
        return self


class APAggregationParametersV1(ContractModel):
    """Complete deterministic Calculation Evidence batches consumed by an aggregation."""

    calculation_evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=249)

    @field_validator("calculation_evidence_ids")
    @classmethod
    def validate_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_identifier(item) for item in value)
        if len(set(normalized)) != len(normalized):
            raise ValueError("calculation evidence IDs must be unique")
        return normalized


class _APAnalyticsRequestBase(ContractModel):
    operation_version: Literal["1.0.0"]
    datasets: tuple[APDatasetReferenceV1, ...]
    rule_snapshot: APPolicyRuleSnapshotV1
    engine_version: Literal["accounts_payable_analytics.v1"]

    @model_validator(mode="after")
    def validate_dataset_identity(self) -> _APAnalyticsRequestBase:
        templates = tuple(item.template_id for item in self.datasets)
        evidence_ids = tuple(item.evidence_id for item in self.datasets)
        if len(set(templates)) != len(templates):
            raise ValueError("AP analytics datasets must use unique templates")
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("AP analytics datasets must use unique Evidence IDs")
        return self


class APDetectionRequestV1(_APAnalyticsRequestBase):
    operation_name: Literal[
        APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION,
        APAnalyticsOperation.INVOICE_PO_VARIANCE_DETECTION,
        APAnalyticsOperation.MISSING_PO_DETECTION,
        APAnalyticsOperation.PAYMENT_TERM_COMPLIANCE_DETECTION,
        APAnalyticsOperation.OVERPAYMENT_DETECTION,
    ]
    parameters: APDetectionParametersV1

    @model_validator(mode="after")
    def validate_detection_datasets(self) -> APDetectionRequestV1:
        dedicated = {
            APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION: (
                APDatabaseTemplate.DUPLICATE_CANDIDATES
            ),
            APAnalyticsOperation.INVOICE_PO_VARIANCE_DETECTION: (
                APDatabaseTemplate.INVOICE_PO_VARIANCE
            ),
            APAnalyticsOperation.MISSING_PO_DETECTION: APDatabaseTemplate.INVOICE_PO_VARIANCE,
            APAnalyticsOperation.PAYMENT_TERM_COMPLIANCE_DETECTION: (
                APDatabaseTemplate.PAYMENT_TERMS
            ),
            APAnalyticsOperation.OVERPAYMENT_DETECTION: APDatabaseTemplate.PAYMENT_AMOUNT,
        }[APAnalyticsOperation(self.operation_name)]
        if {item.template_id for item in self.datasets} != {
            APDatabaseTemplate.INVOICE_POPULATION,
            dedicated,
        }:
            raise ValueError(
                "AP detection requires the common population and its exact dedicated dataset"
            )
        return self


class APExceptionSummaryRequestV1(_APAnalyticsRequestBase):
    operation_name: Literal[APAnalyticsOperation.EXCEPTION_SUMMARY]
    parameters: APAggregationParametersV1

    @model_validator(mode="after")
    def validate_population_dataset(self) -> APExceptionSummaryRequestV1:
        if tuple(item.template_id for item in self.datasets) != (
            APDatabaseTemplate.INVOICE_POPULATION,
        ):
            raise ValueError("AP exception summary requires exactly the common population dataset")
        return self


class APSupplierExceptionRateRequestV1(_APAnalyticsRequestBase):
    operation_name: Literal[APAnalyticsOperation.SUPPLIER_EXCEPTION_RATE]
    parameters: APAggregationParametersV1

    @model_validator(mode="after")
    def validate_population_dataset(self) -> APSupplierExceptionRateRequestV1:
        if tuple(item.template_id for item in self.datasets) != (
            APDatabaseTemplate.INVOICE_POPULATION,
        ):
            raise ValueError(
                "AP supplier exception rate requires exactly the common population dataset"
            )
        return self


APAnalyticsRequestV1: TypeAlias = Annotated[
    APDetectionRequestV1 | APExceptionSummaryRequestV1 | APSupplierExceptionRateRequestV1,
    Field(discriminator="operation_name"),
]


class APExceptionRecordV1(ContractModel):
    """One deterministic AP exception with minimized record-level lineage."""

    exception_id: str
    exception_type: APExceptionType
    invoice_record_key: str
    supplier_id: str
    legal_entity_id: str
    business_unit_id: str
    currency: str
    gross_amount: NonNegativeMoney
    observed_values: JsonObject
    threshold_values: JsonObject
    status: APExceptionStatus
    rule_id: str
    rule_version: str
    rule_set_version: Literal["ap_rules.2026.1"]
    database_evidence_ids: tuple[str, ...]
    calculation_evidence_id: str | None = None
    reason_codes: tuple[str, ...]

    _ids = field_validator(
        "exception_id",
        "invoice_record_key",
        "supplier_id",
        "legal_entity_id",
        "business_unit_id",
        "rule_id",
        "rule_version",
    )(_identifier)
    _currency = field_validator("currency")(_currency)

    @field_validator("database_evidence_ids", "reason_codes")
    @classmethod
    def validate_unique_nonblank_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_identifier(item) for item in value)
        if not normalized or len(set(normalized)) != len(normalized):
            raise ValueError("lineage and reason-code collections must be non-empty and unique")
        return normalized


class APExclusionRecordV1(ContractModel):
    """Reason-coded unsupported or invalid source record retained for coverage."""

    invoice_record_key: str
    supplier_id: str
    legal_entity_id: str
    business_unit_id: str
    currency: str | None = None
    reason_code: str

    _ids = field_validator(
        "invoice_record_key",
        "supplier_id",
        "legal_entity_id",
        "business_unit_id",
        "reason_code",
    )(_identifier)
    _currency = field_validator("currency")(_optional_currency)


class APDuplicateGroupV1(ContractModel):
    """One exact duplicate group and its canonical/noncanonical members."""

    canonical_invoice_record_key: str
    member_invoice_record_keys: tuple[str, ...] = Field(min_length=2)
    supplier_id: str
    normalized_invoice_number: str
    invoice_date: date
    gross_amount: NonNegativeMoney
    currency: str

    _ids = field_validator(
        "canonical_invoice_record_key", "supplier_id", "normalized_invoice_number"
    )(_identifier)
    _currency = field_validator("currency")(_currency)

    @field_validator("member_invoice_record_keys")
    @classmethod
    def validate_members(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_identifier(item) for item in value)
        if tuple(sorted(normalized)) != normalized or len(set(normalized)) != len(normalized):
            raise ValueError("duplicate group members must be unique and sorted")
        return normalized

    @model_validator(mode="after")
    def validate_canonical_member(self) -> APDuplicateGroupV1:
        if self.canonical_invoice_record_key != self.member_invoice_record_keys[0]:
            raise ValueError("duplicate canonical member must be the lexicographically smallest")
        return self


class APSupplierExceptionRateRecordV1(ContractModel):
    """One deterministic supplier review-ordering metric."""

    supplier_id: str
    exception_invoice_count: int = Field(ge=0)
    eligible_invoice_count: int = Field(ge=0)
    supplier_exception_rate: CanonicalRatio | None
    invoice_amount_by_currency: JsonObject
    exception_amount_by_currency: JsonObject
    exclusion_count: int = Field(ge=0)

    _supplier_id = field_validator("supplier_id")(_identifier)

    @model_validator(mode="after")
    def validate_rate_denominator(self) -> APSupplierExceptionRateRecordV1:
        if self.eligible_invoice_count == 0 and self.supplier_exception_rate is not None:
            raise ValueError("zero supplier denominator requires a null rate")
        if self.exception_invoice_count > self.eligible_invoice_count:
            raise ValueError("supplier exception count cannot exceed its eligible population")
        return self


class APAnalyticsResultV1(ContractModel):
    """Common deterministic AP operation result and evidence batching source."""

    operation_name: APAnalyticsOperation
    operation_version: Literal["1.0.0"]
    engine_version: Literal["accounts_payable_analytics.v1"]
    records: tuple[APExceptionRecordV1, ...] = Field(max_length=AP_MAX_EXCEPTION_RECORDS)
    duplicate_groups: tuple[APDuplicateGroupV1, ...] = Field(
        default_factory=tuple, max_length=AP_MAX_EXCEPTION_RECORDS
    )
    supplier_rates: tuple[APSupplierExceptionRateRecordV1, ...] = Field(
        default_factory=tuple, max_length=AP_MAX_SUPPLIERS
    )
    exclusions: tuple[APExclusionRecordV1, ...] = Field(
        default_factory=tuple, max_length=AP_MAX_SOURCE_ROWS
    )
    metrics: JsonObject
    warnings: tuple[str, ...]
    input_row_count: int = Field(ge=0, le=AP_MAX_SOURCE_ROWS)
    eligibility_count: int = Field(ge=0, le=AP_MAX_SOURCE_ROWS)
    exclusion_count: int = Field(ge=0, le=AP_MAX_SOURCE_ROWS)
    exclusion_count_by_reason: dict[str, int]
    rule_ids: tuple[str, ...]
    rule_set_version: Literal["ap_rules.2026.1"]
    manifest_checksum: str
    input_checksums: tuple[str, ...]
    normalization_version: Literal[
        "accounts_payable_normalization.v1", "invoice_number_normalization.v1"
    ]
    precision: Literal["decimal(20,4);ratio(12,8)"]
    rounding_mode: Literal["ROUND_HALF_EVEN"]
    empty_result: bool
    output_checksum: str

    @field_validator("manifest_checksum", "output_checksum")
    @classmethod
    def validate_checksums(cls, value: str) -> str:
        clean = value.strip()
        if _CHECKSUM.fullmatch(clean) is None:
            raise ValueError("AP analytics checksum must be SHA-256")
        return clean

    @field_validator("rule_ids", "input_checksums")
    @classmethod
    def validate_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_identifier(item) for item in value)
        if not normalized or len(set(normalized)) != len(normalized):
            raise ValueError("AP result lineage values must be non-empty and unique")
        return normalized

    @model_validator(mode="after")
    def validate_result_semantics(self) -> APAnalyticsResultV1:
        if self.exclusion_count != len(self.exclusions):
            raise ValueError("exclusion_count must equal the retained exclusion records")
        expected = dict(sorted(Counter(item.reason_code for item in self.exclusions).items()))
        if self.exclusion_count_by_reason != expected:
            raise ValueError("exclusion counts must match retained reason-coded records")
        if self.empty_result != (self.input_row_count == 0):
            raise ValueError("empty_result must describe an empty source population")
        if len({item.exception_id for item in self.records}) != len(self.records):
            raise ValueError("exception IDs must be unique")
        allowed_types = {
            APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION: {
                APExceptionType.EXACT_DUPLICATE_INVOICE
            },
            APAnalyticsOperation.INVOICE_PO_VARIANCE_DETECTION: {
                APExceptionType.PO_AMOUNT_VARIANCE
            },
            APAnalyticsOperation.MISSING_PO_DETECTION: {APExceptionType.MISSING_REQUIRED_PO},
            APAnalyticsOperation.PAYMENT_TERM_COMPLIANCE_DETECTION: {
                APExceptionType.LATE_PAYMENT,
                APExceptionType.MATERIAL_EARLY_PAYMENT,
            },
            APAnalyticsOperation.OVERPAYMENT_DETECTION: {APExceptionType.OVERPAYMENT},
        }.get(self.operation_name)
        if allowed_types is not None and any(
            item.exception_type not in allowed_types for item in self.records
        ):
            raise ValueError("exception type does not belong to the declared AP operation")
        if self.operation_name is APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION:
            if not self.duplicate_groups and self.records:
                raise ValueError("duplicate exceptions require their exact duplicate groups")
        elif self.duplicate_groups:
            raise ValueError("duplicate groups belong only to exact duplicate detection")
        if self.operation_name is APAnalyticsOperation.SUPPLIER_EXCEPTION_RATE:
            if self.records:
                raise ValueError("supplier rate output must not duplicate exception records")
        elif self.supplier_rates:
            raise ValueError("supplier rates belong only to their frozen metric operation")
        return self


AP_ANALYTICS_REQUEST_ADAPTER: TypeAdapter[APAnalyticsRequestV1] = TypeAdapter(APAnalyticsRequestV1)
AP_ANALYTICS_INPUT_SCHEMA = cast(JsonMapping, AP_ANALYTICS_REQUEST_ADAPTER.json_schema())
AP_ANALYTICS_OUTPUT_SCHEMA = cast(JsonMapping, APAnalyticsResultV1.model_json_schema())


__all__ = [
    "AP_ANALYTICS_CONTRACT_PROFILE",
    "AP_ANALYTICS_ENGINE_VERSION",
    "AP_ANALYTICS_INPUT_SCHEMA",
    "AP_ANALYTICS_OPERATION_VERSION",
    "AP_ANALYTICS_OUTPUT_SCHEMA",
    "AP_ANALYTICS_REQUEST_ADAPTER",
    "AP_ANALYTICS_TOOL_VERSION",
    "AP_CALCULATION_BATCH_SIZE",
    "AP_MAX_EXCEPTION_RECORDS",
    "AP_MAX_SOURCE_ROWS",
    "APAnalyticsOperation",
    "APAnalyticsRequestV1",
    "APAnalyticsResultV1",
    "APDatabaseTemplate",
    "APDatasetReferenceV1",
    "APDetectionParametersV1",
    "APDetectionRequestV1",
    "APDuplicateGroupV1",
    "APDuplicateInvoiceRowV1",
    "APExceptionRecordV1",
    "APExceptionStatus",
    "APExceptionSummaryRequestV1",
    "APExclusionRecordV1",
    "APInvoicePOVarianceRowV1",
    "APInvoicePopulationRowV1",
    "APPaymentAmountRowV1",
    "APPaymentTermsRowV1",
    "APPolicyRuleSnapshotV1",
    "APRuleEvidenceReferenceV1",
    "APSupplierExceptionRateRecordV1",
    "APSupplierExceptionRateRequestV1",
    "INVOICE_NUMBER_NORMALIZATION_VERSION",
]
