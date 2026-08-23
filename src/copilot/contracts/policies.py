"""Versioned contracts for controlled policy knowledge and executable AP rules."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from copilot.contracts.base import ImmutableContractModel
from copilot.contracts.validators import validate_identifier, validate_utc_datetime

_CHECKSUM_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_NAMESPACE_PATTERN = re.compile(r"^tenant/([^/]+)/finance/accounts-payable/v1$")
_TENANT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class APPolicyDocumentFamily(StrEnum):
    """The four controlled document families frozen for AP v1."""

    ACCOUNTS_PAYABLE = "ACCOUNTS_PAYABLE_POLICY"
    PROCUREMENT_AND_PO = "PROCUREMENT_AND_PURCHASE_ORDER_POLICY"
    INVOICE_APPROVAL = "INVOICE_APPROVAL_AND_DELEGATION_POLICY"
    PAYMENT_TERMS = "PAYMENT_TERMS_POLICY"


class APPolicyRuleKind(StrEnum):
    """Executable rule kinds frozen by ``accounts-payable-design.v1.0``."""

    PO_REQUIRED_AMOUNT = "PO_REQUIRED_AMOUNT"
    PO_VARIANCE_TOLERANCE = "PO_VARIANCE_TOLERANCE"
    MATERIALITY_AMOUNT = "MATERIALITY_AMOUNT"
    MATERIAL_EARLY_DAYS = "MATERIAL_EARLY_DAYS"
    OVERPAYMENT_TOLERANCE = "OVERPAYMENT_TOLERANCE"


def _checksum(value: str) -> str:
    clean = value.strip()
    if _CHECKSUM_PATTERN.fullmatch(clean) is None:
        raise ValueError("checksum must be a lowercase sha256 value")
    return clean


def _tenant_identifier(value: str) -> str:
    clean = value.strip()
    if _TENANT_PATTERN.fullmatch(clean) is None or clean in {".", ".."}:
        raise ValueError("tenant_id must be a bounded safe identifier")
    return clean


class CurrencyAmountV1(ImmutableContractModel):
    """One non-negative governed amount for one currency."""

    currency: str
    amount: Decimal = Field(ge=Decimal("0"), max_digits=20, decimal_places=4)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        clean = value.strip().upper()
        if _CURRENCY_PATTERN.fullmatch(clean) is None:
            raise ValueError("currency must be an uppercase three-letter code")
        return clean


class APPolicyRuleBindingV1(ImmutableContractModel):
    """Exact immutable link from one executable rule to approved policy wording."""

    document_id: str
    document_version: str
    chunk_id: str
    page: int = Field(ge=1)
    document_checksum: str
    excerpt_checksum: str

    @field_validator("document_id", "document_version", "chunk_id")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("document_checksum", "excerpt_checksum")
    @classmethod
    def validate_checksums(cls, value: str) -> str:
        return _checksum(value)


class _APPolicyRuleBase(ImmutableContractModel):
    rule_id: str
    rule_version: str
    effective_from: date
    effective_to: date
    invoice_types: tuple[Literal["STANDARD"], ...] = ("STANDARD",)
    legal_entity_ids: tuple[str, ...] = ()
    binding: APPolicyRuleBindingV1

    @field_validator("rule_id", "rule_version")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("legal_entity_ids")
    @classmethod
    def validate_legal_entities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(validate_identifier(item) for item in value)
        if len(set(normalized)) != len(normalized):
            raise ValueError("legal_entity_ids must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_effective_range(self) -> _APPolicyRuleBase:
        if self.effective_to < self.effective_from:
            raise ValueError("rule effective_to must not precede effective_from")
        return self


def _validate_currency_amounts(
    values: tuple[CurrencyAmountV1, ...],
) -> tuple[CurrencyAmountV1, ...]:
    if not values:
        raise ValueError("currency amounts must not be empty")
    currencies = tuple(item.currency for item in values)
    if len(set(currencies)) != len(currencies):
        raise ValueError("currency amounts must contain unique currencies")
    return values


class PORequiredAmountRuleV1(_APPolicyRuleBase):
    kind: Literal[APPolicyRuleKind.PO_REQUIRED_AMOUNT]
    minimum_amounts: tuple[CurrencyAmountV1, ...]

    _amounts = field_validator("minimum_amounts")(_validate_currency_amounts)


class POVarianceToleranceRuleV1(_APPolicyRuleBase):
    kind: Literal[APPolicyRuleKind.PO_VARIANCE_TOLERANCE]
    allowed_variance_rate: Decimal = Field(
        ge=Decimal("0"), le=Decimal("1"), max_digits=12, decimal_places=8
    )
    allowed_variance_amounts: tuple[CurrencyAmountV1, ...]

    _amounts = field_validator("allowed_variance_amounts")(_validate_currency_amounts)


class MaterialityAmountRuleV1(_APPolicyRuleBase):
    kind: Literal[APPolicyRuleKind.MATERIALITY_AMOUNT]
    thresholds: tuple[CurrencyAmountV1, ...]

    _amounts = field_validator("thresholds")(_validate_currency_amounts)


class MaterialEarlyDaysRuleV1(_APPolicyRuleBase):
    kind: Literal[APPolicyRuleKind.MATERIAL_EARLY_DAYS]
    days: int = Field(ge=0, le=365)


class OverpaymentToleranceRuleV1(_APPolicyRuleBase):
    kind: Literal[APPolicyRuleKind.OVERPAYMENT_TOLERANCE]
    tolerances: tuple[CurrencyAmountV1, ...]

    _amounts = field_validator("tolerances")(_validate_currency_amounts)


APPolicyRuleV1 = Annotated[
    PORequiredAmountRuleV1
    | POVarianceToleranceRuleV1
    | MaterialityAmountRuleV1
    | MaterialEarlyDaysRuleV1
    | OverpaymentToleranceRuleV1,
    Field(discriminator="kind"),
]


class APPolicyRuleManifestV1(ImmutableContractModel):
    """Tenant-bound executable AP rules and their exact policy bindings."""

    schema_version: Literal["ap-policy-rule-manifest.v1"]
    policy_profile: Literal["accounts_payable_policy.v1"]
    rule_set_id: Literal["accounts-payable-v1"]
    rule_set_version: Literal["ap_rules.2026.1"]
    tenant_id: str
    effective_from: date
    effective_to: date
    approved_by: str
    approved_at: datetime
    rules: tuple[APPolicyRuleV1, ...]
    manifest_checksum: str

    @field_validator("tenant_id", "approved_by")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant(cls, value: str) -> str:
        return _tenant_identifier(value)

    @field_validator("approved_at")
    @classmethod
    def validate_approval_time(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)

    @field_validator("manifest_checksum")
    @classmethod
    def validate_checksum(cls, value: str) -> str:
        return _checksum(value)

    @model_validator(mode="after")
    def validate_manifest(self) -> APPolicyRuleManifestV1:
        if self.effective_to < self.effective_from:
            raise ValueError("manifest effective_to must not precede effective_from")
        expected = set(APPolicyRuleKind)
        actual = {rule.kind for rule in self.rules}
        if actual != expected or len(self.rules) != len(expected):
            raise ValueError("ap_rules.2026.1 must contain each frozen AP rule kind exactly once")
        rule_ids = tuple(rule.rule_id for rule in self.rules)
        if len(set(rule_ids)) != len(rule_ids):
            raise ValueError("rule IDs must be unique")
        for rule in self.rules:
            if rule.effective_from < self.effective_from or rule.effective_to > self.effective_to:
                raise ValueError("rule effective range must be inside the manifest range")
        return self


class PolicyChunkDescriptorV1(ImmutableContractModel):
    """Expected immutable chunk inside one controlled policy fixture."""

    chunk_id: str
    page: int = Field(ge=1)
    excerpt_checksum: str

    @field_validator("chunk_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("excerpt_checksum")
    @classmethod
    def validate_checksum(cls, value: str) -> str:
        return _checksum(value)


class ControlledPolicyDocumentV1(ImmutableContractModel):
    """Metadata and declared checksums for one controlled source document."""

    family: APPolicyDocumentFamily
    document_id: str
    document_version: str
    effective_from: date
    effective_to: date
    classification: Literal["INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    owner: str
    approved_by: str
    approved_at: datetime
    language: Literal["en-US", "zh-CN"]
    content_path: str
    checksum: str
    chunks: tuple[PolicyChunkDescriptorV1, ...]

    @field_validator("approved_by", "document_id", "document_version", "owner")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("approved_at")
    @classmethod
    def validate_approval_time(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)

    @field_validator("checksum")
    @classmethod
    def validate_checksum(cls, value: str) -> str:
        return _checksum(value)

    @field_validator("content_path")
    @classmethod
    def validate_content_path(cls, value: str) -> str:
        clean = value.strip()
        if not clean or clean.startswith(("/", "\\")) or ".." in clean.split("/"):
            raise ValueError("content_path must be a safe relative path")
        return clean

    @model_validator(mode="after")
    def validate_document(self) -> ControlledPolicyDocumentV1:
        if self.effective_to < self.effective_from:
            raise ValueError("document effective_to must not precede effective_from")
        chunk_ids = tuple(chunk.chunk_id for chunk in self.chunks)
        if not chunk_ids or len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("document chunks must be non-empty and unique")
        return self


class ControlledPolicyCorpusManifestV1(ImmutableContractModel):
    """Approved four-document AP corpus and tenant namespace."""

    schema_version: Literal["ap-policy-corpus-manifest.v1"]
    policy_profile: Literal["accounts_payable_policy.v1"]
    collection_id: Literal["accounts-payable-policy-v1"]
    approved_collection_id: Literal["accounts-payable-policy-v1"]
    tenant_id: str
    namespace: str
    documents: tuple[ControlledPolicyDocumentV1, ...]
    corpus_checksum: str

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant(cls, value: str) -> str:
        return _tenant_identifier(value)

    @field_validator("corpus_checksum")
    @classmethod
    def validate_checksum(cls, value: str) -> str:
        return _checksum(value)

    @model_validator(mode="after")
    def validate_corpus(self) -> ControlledPolicyCorpusManifestV1:
        namespace = _NAMESPACE_PATTERN.fullmatch(self.namespace)
        if namespace is None or namespace.group(1) != self.tenant_id:
            raise ValueError("policy namespace must be bound to the exact tenant")
        expected = set(APPolicyDocumentFamily)
        actual = {document.family for document in self.documents}
        if actual != expected or len(self.documents) != len(expected):
            raise ValueError("AP v1 corpus must contain each controlled document family once")
        document_ids = tuple(document.document_id for document in self.documents)
        if len(set(document_ids)) != len(document_ids):
            raise ValueError("document IDs must be unique")
        return self


class PublishedPolicyDocumentV1(ImmutableContractModel):
    """Minimized document metadata included in an immutable index snapshot."""

    document_id: str
    document_version: str
    checksum: str
    chunk_ids: tuple[str, ...]

    @field_validator("document_id", "document_version")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("checksum")
    @classmethod
    def validate_checksum(cls, value: str) -> str:
        return _checksum(value)


class APPolicySnapshotV1(ImmutableContractModel):
    """Immutable metadata for one atomically published AP policy index generation."""

    schema_version: Literal["ap-policy-snapshot.v1"]
    snapshot_id: str
    index_revision: str
    tenant_id: str
    namespace: str
    collection_id: Literal["accounts-payable-policy-v1"]
    policy_profile: Literal["accounts_payable_policy.v1"]
    rule_set_id: Literal["accounts-payable-v1"]
    rule_set_version: Literal["ap_rules.2026.1"]
    manifest_checksum: str
    corpus_checksum: str
    payload_checksum: str
    documents: tuple[PublishedPolicyDocumentV1, ...]
    binding_count: int = Field(ge=1)
    published_at: datetime
    publication_checksum: str

    @field_validator("snapshot_id", "index_revision")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant(cls, value: str) -> str:
        return _tenant_identifier(value)

    @field_validator(
        "manifest_checksum", "corpus_checksum", "payload_checksum", "publication_checksum"
    )
    @classmethod
    def validate_checksums(cls, value: str) -> str:
        return _checksum(value)

    @field_validator("published_at")
    @classmethod
    def validate_publication_time(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


__all__ = [
    "APPolicyDocumentFamily",
    "APPolicyRuleBindingV1",
    "APPolicyRuleKind",
    "APPolicyRuleManifestV1",
    "APPolicyRuleV1",
    "APPolicySnapshotV1",
    "ControlledPolicyCorpusManifestV1",
    "ControlledPolicyDocumentV1",
    "CurrencyAmountV1",
    "MaterialEarlyDaysRuleV1",
    "MaterialityAmountRuleV1",
    "OverpaymentToleranceRuleV1",
    "PORequiredAmountRuleV1",
    "POVarianceToleranceRuleV1",
    "PolicyChunkDescriptorV1",
    "PublishedPolicyDocumentV1",
]
