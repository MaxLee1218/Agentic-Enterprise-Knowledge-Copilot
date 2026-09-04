"""Deterministic validation and constraint merging for natural-language task intake."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from copilot.contracts import ArtifactType, JsonObject, MoneyThreshold, TaskType
from copilot.security import SensitiveDataRegistry

MetadataValue: TypeAlias = JsonValue


class RequestSource(StrEnum):
    """Trusted transport that submitted a task."""

    API = "api"
    CLI = "cli"
    INTERNAL = "internal"


class TaskOutputFormat(StrEnum):
    """Public output choices mapped onto the frozen Artifact contract."""

    PDF = "pdf"
    JSON = "json"

    @property
    def artifact_type(self) -> ArtifactType:
        """Return the Supplier Quality Artifact retained for backward compatibility."""
        return self.artifact_type_for(TaskType.SUPPLIER_QUALITY_ANALYSIS_V1)

    def artifact_type_for(self, task_type: TaskType) -> ArtifactType:
        """Map a transport format through the trusted versioned task type."""
        return {
            TaskType.SUPPLIER_QUALITY_ANALYSIS_V1: {
                TaskOutputFormat.PDF: ArtifactType.QUALITY_ANALYSIS_REPORT_PDF,
                TaskOutputFormat.JSON: ArtifactType.QUALITY_ANALYSIS_REPORT_JSON,
            },
            TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1: {
                TaskOutputFormat.PDF: ArtifactType.ACCOUNTS_PAYABLE_REPORT_PDF,
                TaskOutputFormat.JSON: ArtifactType.ACCOUNTS_PAYABLE_REPORT_JSON,
            },
        }[task_type][self]


class TaskDomainResolutionStatus(StrEnum):
    """Closed non-executable outcomes produced before domain-specific understanding."""

    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"


class TaskDomainResolution(BaseModel):
    """Deterministic supported-domain classification derived from untrusted task text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: TaskDomainResolutionStatus
    task_type: TaskType | None = None
    reason_code: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_outcome(self) -> TaskDomainResolution:
        if (self.status is TaskDomainResolutionStatus.RESOLVED) != (self.task_type is not None):
            raise ValueError("only RESOLVED domain outcomes contain a task_type")
        return self


class NaturalLanguageTaskCommand(BaseModel):
    """Framework-independent application command accepted from API and CLI adapters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task: str
    task_type: TaskType | None = None
    output_format: TaskOutputFormat | None = None
    max_steps: int | None = Field(default=None, ge=1)
    read_only: bool | None = None
    require_approval: bool | None = None
    session_id: str | None = Field(default=None, min_length=1, max_length=200)
    metadata: dict[str, MetadataValue] = Field(default_factory=dict)
    source: RequestSource
    trace_id: str | None = Field(default=None, min_length=1, max_length=200)


class TrustedCallerContext(BaseModel):
    """Identity and authorization facts supplied by authentication, never user text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    data_scope: tuple[str, ...] = Field(min_length=1)
    supplier_ids: tuple[str, ...] = ()
    legal_entity_ids: tuple[str, ...] = ()
    business_unit_ids: tuple[str, ...] = ()
    currency_scope: tuple[str, ...] = ()
    assigned_task_ids: tuple[str, ...] = ()
    allowed_task_types: tuple[TaskType, ...] = (TaskType.SUPPLIER_QUALITY_ANALYSIS_V1,)
    roles: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    authentication_source: str = Field(default="demo", min_length=1)
    authenticated: bool = True
    is_demo_identity: bool = True
    purpose: str = Field(default="supplier_quality_analysis.v1", min_length=1)
    attributes: dict[str, MetadataValue] = Field(default_factory=dict)
    policy_rule_set_id: str | None = None
    policy_rule_set_version: str | None = None
    policy_manifest_checksum: str | None = None
    policy_materiality: tuple[MoneyThreshold, ...] = ()
    policy_snapshot_at: datetime | None = None
    policy_requires_approval: bool = False
    policy_forces_read_only: bool = True

    @model_validator(mode="after")
    def validate_domain_authority(self) -> TrustedCallerContext:
        """Require purpose to select one explicitly authorized versioned TaskType."""
        if len(set(self.allowed_task_types)) != len(self.allowed_task_types):
            raise ValueError("allowed_task_types must be unique")
        try:
            selected = TaskType(self.purpose)
        except ValueError as exc:
            raise ValueError("purpose must be a supported versioned task type") from exc
        if selected not in self.allowed_task_types:
            raise ValueError("purpose is not present in allowed_task_types")
        return self


class TrustedTaskContext(BaseModel):
    """Trusted execution envelope carried beside the untrusted TaskRequest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    session_id: str
    user_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    data_scope: tuple[str, ...]
    authorized_supplier_ids: tuple[str, ...] = ()
    authorized_legal_entity_ids: tuple[str, ...] = ()
    authorized_business_unit_ids: tuple[str, ...] = ()
    authorized_currency_scope: tuple[str, ...] = ()
    policy_rule_set_id: str | None = None
    policy_rule_set_version: str | None = None
    policy_manifest_checksum: str | None = None
    policy_materiality: tuple[MoneyThreshold, ...] = ()
    policy_snapshot_at: datetime | None = None
    roles: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    authentication_source: str = Field(default="demo", min_length=1)
    authenticated: bool = True
    is_demo_identity: bool = True
    purpose: str = Field(default="supplier_quality_analysis.v1", min_length=1)
    task_type: TaskType = TaskType.SUPPLIER_QUALITY_ANALYSIS_V1
    output_format: ArtifactType | None = None
    max_steps: int = Field(ge=1)
    read_only: bool
    require_approval: bool
    deadline_at: datetime
    request_source: RequestSource
    task_text_hash: str = Field(min_length=64, max_length=64)
    task_text_length: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_domain_binding(self) -> TrustedTaskContext:
        """Prevent purpose/task-type substitution after trusted intake."""
        if self.purpose != self.task_type.value:
            raise ValueError("purpose must match the trusted task_type")
        return self


@dataclass(frozen=True, slots=True)
class IntakeLimits:
    """Server-owned validation and execution limits."""

    max_task_text_length: int
    max_metadata_bytes: int
    max_metadata_depth: int
    max_metadata_items: int
    max_task_steps: int
    max_total_execution_seconds: int
    force_read_only: bool = True
    require_approval: bool = False


@dataclass(frozen=True, slots=True)
class EffectiveExecutionConstraints:
    """Result of the deterministic, tightening-only constraint merge."""

    max_steps: int
    read_only: bool
    require_approval: bool


class TaskIntakeValidationError(ValueError):
    """Safe input failure raised before persistence or model execution."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_SUPPLIER_QUALITY_SIGNALS = (
    r"\bsupplier\s+quality\b",
    r"\b(?:analyze|analyse|investigate|review)\b.{0,48}\bsuppliers?\b",
    r"\bquality\s+(?:issue|issues|analysis|deviation|deviations|trend|trends)\b",
    r"\bdefect(?:s|\s+rate|\s+rates)?\b",
    r"\bnonconform(?:ance|ances|ing)?\b",
    r"\breject(?:ion|ions|ed)?\s+(?:rate|rates|lot|lots)?\b",
    r"供应商质量",
    r"供应商(?:问题|分析|偏差)",
    r"质量(?:问题|分析|偏差|趋势)",
    r"(?:缺陷|不良)率?",
)

_ACCOUNTS_PAYABLE_SIGNALS = (
    r"\baccounts?\s+payable\b",
    r"\bap\b",
    r"\ba\s*/\s*p\b",
    r"\binvoice(?:s|\s+exception|\s+exceptions|\s+compliance)?\b",
    r"\bpayment\s+(?:exception|exceptions|compliance|term|terms|timing)\b",
    r"\boverpayment(?:s|\s+exception|\s+exceptions)?\b",
    r"\bthree[- ]way\s+match(?:ing)?\b",
    r"\bpurchase\s+order(?:s)?\b",
    r"应付账款",
    r"发票",
    r"付款(?:异常|合规|条款)",
)

_PDF_SIGNAL = re.compile(r"(?i)(?:\bpdf\b|PDF报告|PDF 报告)")
_JSON_SIGNAL = re.compile(r"(?i)(?:\bjson\b|JSON报告|JSON 报告)")
_UNSUPPORTED_OUTPUT_SIGNAL = re.compile(
    r"(?i)(?:\b(?:csv|xlsx?|spreadsheet|powerpoint|pptx?|docx?|word)\b|电子表格|幻灯片)"
)


def resolve_task_domain(
    task_text: str,
    *,
    explicit_task_type: TaskType | None = None,
) -> TaskDomainResolution:
    """Resolve one enabled domain without consulting caller purpose or selecting a tool."""
    if explicit_task_type is not None:
        return TaskDomainResolution(
            status=TaskDomainResolutionStatus.RESOLVED,
            task_type=explicit_task_type,
            reason_code="EXPLICIT_COMPATIBILITY_TASK_TYPE",
        )
    supplier_quality = any(
        re.search(pattern, task_text, re.IGNORECASE) for pattern in _SUPPLIER_QUALITY_SIGNALS
    )
    accounts_payable = any(
        re.search(pattern, task_text, re.IGNORECASE) for pattern in _ACCOUNTS_PAYABLE_SIGNALS
    )
    if supplier_quality and accounts_payable:
        return TaskDomainResolution(
            status=TaskDomainResolutionStatus.AMBIGUOUS,
            reason_code="MULTIPLE_SUPPORTED_DOMAINS",
        )
    if supplier_quality:
        return TaskDomainResolution(
            status=TaskDomainResolutionStatus.RESOLVED,
            task_type=TaskType.SUPPLIER_QUALITY_ANALYSIS_V1,
            reason_code="SUPPLIER_QUALITY_LANGUAGE_MATCH",
        )
    if accounts_payable:
        return TaskDomainResolution(
            status=TaskDomainResolutionStatus.RESOLVED,
            task_type=TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1,
            reason_code="ACCOUNTS_PAYABLE_LANGUAGE_MATCH",
        )
    return TaskDomainResolution(
        status=TaskDomainResolutionStatus.UNSUPPORTED,
        reason_code="NO_SUPPORTED_DOMAIN_MATCH",
    )


def resolve_output_format(
    task_text: str,
    *,
    explicit_output_format: TaskOutputFormat | None = None,
) -> tuple[TaskOutputFormat, str]:
    """Extract an explicit supported format or apply the manifest-owned PDF default."""
    pdf_requested = _PDF_SIGNAL.search(task_text) is not None
    json_requested = _JSON_SIGNAL.search(task_text) is not None
    if pdf_requested and json_requested:
        raise TaskIntakeValidationError(
            "AMBIGUOUS_OUTPUT_FORMAT",
            "Request one output format: PDF or JSON.",
        )
    text_format = (
        TaskOutputFormat.PDF if pdf_requested else TaskOutputFormat.JSON if json_requested else None
    )
    if explicit_output_format is not None:
        if text_format is not None and text_format is not explicit_output_format:
            raise TaskIntakeValidationError(
                "CONFLICTING_OUTPUT_FORMAT",
                "The structured output option conflicts with the natural-language request.",
            )
        return explicit_output_format, "EXPLICIT_COMPATIBILITY_OPTION"
    if text_format is not None:
        return text_format, "NATURAL_LANGUAGE_REQUEST"
    if _UNSUPPORTED_OUTPUT_SIGNAL.search(task_text) is not None:
        raise TaskIntakeValidationError(
            "UNSUPPORTED_OUTPUT_FORMAT",
            "This workspace currently supports PDF and JSON reports.",
        )
    return TaskOutputFormat.PDF, "DOMAIN_DEFAULT"


def validate_task_text(value: str, *, max_length: int) -> str:
    """Trim only surrounding whitespace and reject unsafe control characters."""
    normalized = value.strip()
    if not normalized:
        raise TaskIntakeValidationError("INVALID_TASK_INPUT", "Task text must not be empty.")
    if len(normalized) > max_length:
        raise TaskIntakeValidationError(
            "TASK_TOO_LONG",
            f"Task text exceeds the configured limit of {max_length} characters.",
        )
    for character in normalized:
        if character in {"\n", "\r", "\t"}:
            continue
        if character == "\x00" or unicodedata.category(character) in {"Cc", "Cs"}:
            raise TaskIntakeValidationError(
                "INVALID_TASK_INPUT",
                "Task text contains a disallowed control character.",
            )
    return normalized


def sanitize_metadata(
    metadata: dict[str, MetadataValue],
    *,
    max_bytes: int,
    max_depth: int,
    max_items: int,
) -> JsonObject:
    """Validate bounded JSON metadata without changing its values."""
    item_count = 0
    sensitive_registry = SensitiveDataRegistry()

    def visit(value: MetadataValue, depth: int) -> None:
        nonlocal item_count
        if depth > max_depth:
            raise TaskIntakeValidationError(
                "INVALID_TASK_OPTION", "Task metadata exceeds the nesting-depth limit."
            )
        if isinstance(value, dict):
            item_count += len(value)
            if item_count > max_items:
                raise TaskIntakeValidationError(
                    "INVALID_TASK_OPTION", "Task metadata contains too many items."
                )
            for key, child in value.items():
                if not key or len(key) > 200 or any(ord(char) < 32 for char in key):
                    raise TaskIntakeValidationError(
                        "INVALID_TASK_OPTION", "Task metadata contains an invalid key."
                    )
                if sensitive_registry.policy_for(key) is not None:
                    raise TaskIntakeValidationError(
                        "INVALID_TASK_OPTION",
                        "Task metadata must not contain sensitive credential fields.",
                    )
                visit(child, depth + 1)
        elif isinstance(value, list):
            item_count += len(value)
            if item_count > max_items:
                raise TaskIntakeValidationError(
                    "INVALID_TASK_OPTION", "Task metadata contains too many items."
                )
            for child in value:
                visit(child, depth + 1)
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            raise TaskIntakeValidationError(
                "INVALID_TASK_OPTION", "Task metadata must contain JSON values only."
            )

    visit(metadata, 1)
    try:
        encoded = json.dumps(
            metadata,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TaskIntakeValidationError(
            "INVALID_TASK_OPTION", "Task metadata must contain finite JSON values only."
        ) from exc
    if len(encoded) > max_bytes:
        raise TaskIntakeValidationError(
            "INVALID_TASK_OPTION", "Task metadata exceeds the configured size limit."
        )
    return JsonObject(metadata)


def merge_execution_constraints(
    *,
    limits: IntakeLimits,
    caller: TrustedCallerContext,
    requested_max_steps: int | None,
    requested_read_only: bool | None,
    requested_approval: bool | None,
) -> EffectiveExecutionConstraints:
    """Merge constraints using system/policy precedence and tightening-only caller options."""
    max_steps = min(requested_max_steps or limits.max_task_steps, limits.max_task_steps)
    read_only = (
        limits.force_read_only or caller.policy_forces_read_only or requested_read_only is True
    )
    require_approval = (
        limits.require_approval or caller.policy_requires_approval or requested_approval is True
    )
    return EffectiveExecutionConstraints(
        max_steps=max_steps,
        read_only=read_only,
        require_approval=require_approval,
    )


__all__ = [
    "EffectiveExecutionConstraints",
    "IntakeLimits",
    "NaturalLanguageTaskCommand",
    "RequestSource",
    "TaskIntakeValidationError",
    "TaskOutputFormat",
    "TrustedCallerContext",
    "TrustedTaskContext",
    "merge_execution_constraints",
    "sanitize_metadata",
    "validate_task_text",
]
