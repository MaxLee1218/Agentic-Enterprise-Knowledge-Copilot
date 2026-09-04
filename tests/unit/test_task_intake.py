"""Natural-language transport validation and deterministic intake constraints."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from copilot.api.schemas.tasks import NaturalLanguageTaskSubmission
from copilot.contracts import ArtifactType, TaskType
from copilot.services.task_intake import (
    IntakeLimits,
    TaskDomainResolutionStatus,
    TaskIntakeValidationError,
    TaskOutputFormat,
    TrustedCallerContext,
    merge_execution_constraints,
    resolve_output_format,
    resolve_task_domain,
    sanitize_metadata,
    validate_task_text,
)

LIMITS = IntakeLimits(
    max_task_text_length=100,
    max_metadata_bytes=100,
    max_metadata_depth=3,
    max_metadata_items=5,
    max_task_steps=10,
    max_total_execution_seconds=300,
)
CALLER = TrustedCallerContext(
    user_id="U-1",
    tenant_id="TENANT-1",
    data_scope=("quality.v1",),
)


@pytest.mark.parametrize(
    "task",
    (
        "分析 2026 年第二季度供应商质量偏差。",
        "Analyze Q2 2026 supplier quality deviations.",
        "Analysiere die Lieferantenqualität im 2. Quartal 2026.",
        'Analyze "Q2" supplier quality — 2026.',
    ),
)
def test_transport_accepts_normal_unicode_tasks(task: str) -> None:
    assert NaturalLanguageTaskSubmission(task=task).task == task


@pytest.mark.parametrize("task", ("", " \n\t ", "bad\x00task", "bad\x07task"))
def test_transport_rejects_blank_or_control_bearing_tasks(task: str) -> None:
    with pytest.raises(ValidationError):
        NaturalLanguageTaskSubmission(task=task)


def test_settings_owned_text_limit_and_metadata_bounds() -> None:
    with pytest.raises(TaskIntakeValidationError, match="configured limit"):
        validate_task_text("x" * 101, max_length=100)
    with pytest.raises(TaskIntakeValidationError, match="nesting-depth"):
        sanitize_metadata(
            {"a": {"b": {"c": {"d": True}}}},
            max_bytes=100,
            max_depth=3,
            max_items=10,
        )
    with pytest.raises(TaskIntakeValidationError, match="size limit"):
        sanitize_metadata(
            {"value": "x" * 100},
            max_bytes=20,
            max_depth=3,
            max_items=10,
        )


def test_constraint_merge_only_tightens_policy() -> None:
    tightened = merge_execution_constraints(
        limits=LIMITS,
        caller=CALLER,
        requested_max_steps=5,
        requested_read_only=False,
        requested_approval=False,
    )
    assert tightened.max_steps == 5
    assert tightened.read_only is True

    bounded = merge_execution_constraints(
        limits=LIMITS,
        caller=CALLER.model_copy(
            update={"policy_requires_approval": True, "policy_forces_read_only": True}
        ),
        requested_max_steps=100,
        requested_read_only=False,
        requested_approval=False,
    )
    assert bounded.max_steps == 10
    assert bounded.read_only is True
    assert bounded.require_approval is True


@pytest.mark.parametrize("key", ("access_token", "Authorization", "password_hash"))
def test_sensitive_or_authority_metadata_is_rejected(key: str) -> None:
    with pytest.raises(TaskIntakeValidationError, match="sensitive credential"):
        sanitize_metadata(
            {"nested": {key: "fixed-stage15-value"}},
            max_bytes=100,
            max_depth=3,
            max_items=10,
        )


def test_datetime_fixture_is_timezone_aware() -> None:
    assert datetime(2026, 7, 31, tzinfo=UTC).utcoffset() is not None


def test_trusted_purpose_must_select_an_explicitly_allowed_task_type() -> None:
    with pytest.raises(ValidationError, match="not present in allowed_task_types"):
        TrustedCallerContext(
            user_id="U-AP",
            tenant_id="TENANT-A",
            data_scope=("accounts_payable.v1",),
            purpose=TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1.value,
        )

    caller = TrustedCallerContext(
        user_id="U-AP",
        tenant_id="TENANT-A",
        data_scope=("accounts_payable.v1",),
        purpose=TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1.value,
        allowed_task_types=(TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1,),
    )
    assert caller.purpose == caller.allowed_task_types[0].value


def test_output_format_maps_through_trusted_task_type() -> None:
    assert (
        TaskOutputFormat.JSON.artifact_type_for(TaskType.SUPPLIER_QUALITY_ANALYSIS_V1)
        is ArtifactType.QUALITY_ANALYSIS_REPORT_JSON
    )
    assert (
        TaskOutputFormat.JSON.artifact_type_for(TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1)
        is ArtifactType.ACCOUNTS_PAYABLE_REPORT_JSON
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        (
            "Analyze supplier quality issues in Q2.",
            TaskType.SUPPLIER_QUALITY_ANALYSIS_V1,
        ),
        (
            "Investigate duplicate invoice exceptions for Accounts Payable.",
            TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1,
        ),
        ("分析第二季度供应商质量偏差。", TaskType.SUPPLIER_QUALITY_ANALYSIS_V1),
        ("调查应付账款发票异常。", TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1),
    ),
)
def test_natural_language_resolves_only_enabled_domains(
    text: str,
    expected: TaskType,
) -> None:
    resolution = resolve_task_domain(text)
    assert resolution.status is TaskDomainResolutionStatus.RESOLVED
    assert resolution.task_type is expected
    assert resolution.reason_code


def test_domain_resolution_fails_closed_for_ambiguous_and_unsupported_text() -> None:
    ambiguous = resolve_task_domain(
        "Compare supplier quality defects with Accounts Payable invoice exceptions."
    )
    unsupported = resolve_task_domain("Send an email asking for a refund.")
    assert ambiguous.status is TaskDomainResolutionStatus.AMBIGUOUS
    assert ambiguous.task_type is None
    assert unsupported.status is TaskDomainResolutionStatus.UNSUPPORTED
    assert unsupported.task_type is None


def test_output_format_is_extracted_or_defaults_deterministically_to_pdf() -> None:
    assert resolve_output_format("Analyze supplier quality.") == (
        TaskOutputFormat.PDF,
        "DOMAIN_DEFAULT",
    )
    assert resolve_output_format("Analyze Accounts Payable and return JSON.") == (
        TaskOutputFormat.JSON,
        "NATURAL_LANGUAGE_REQUEST",
    )
    with pytest.raises(TaskIntakeValidationError, match="PDF or JSON"):
        resolve_output_format("Return PDF and JSON.")
    with pytest.raises(TaskIntakeValidationError, match="supports PDF and JSON"):
        resolve_output_format("Export an Excel spreadsheet.")
