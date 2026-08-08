"""Deterministic validation and constraint merging for natural-language task intake."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from copilot.contracts import ArtifactType, JsonObject
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
        """Return the corresponding frozen Artifact type."""
        return {
            TaskOutputFormat.PDF: ArtifactType.QUALITY_ANALYSIS_REPORT_PDF,
            TaskOutputFormat.JSON: ArtifactType.QUALITY_ANALYSIS_REPORT_JSON,
        }[self]


class NaturalLanguageTaskCommand(BaseModel):
    """Framework-independent application command accepted from API and CLI adapters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task: str
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
    roles: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    authentication_source: str = Field(default="demo", min_length=1)
    authenticated: bool = True
    is_demo_identity: bool = True
    purpose: str = Field(default="supplier_quality_analysis.v1", min_length=1)
    attributes: dict[str, MetadataValue] = Field(default_factory=dict)
    policy_requires_approval: bool = False
    policy_forces_read_only: bool = True


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
    roles: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    authentication_source: str = Field(default="demo", min_length=1)
    authenticated: bool = True
    is_demo_identity: bool = True
    purpose: str = Field(default="supplier_quality_analysis.v1", min_length=1)
    output_format: ArtifactType | None = None
    max_steps: int = Field(ge=1)
    read_only: bool
    require_approval: bool
    deadline_at: datetime
    request_source: RequestSource
    task_text_hash: str = Field(min_length=64, max_length=64)
    task_text_length: int = Field(ge=1)


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
