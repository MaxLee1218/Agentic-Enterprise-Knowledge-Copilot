"""Natural-language transport validation and deterministic intake constraints."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from copilot.api.schemas.tasks import NaturalLanguageTaskSubmission
from copilot.services.task_intake import (
    IntakeLimits,
    TaskIntakeValidationError,
    TrustedCallerContext,
    merge_execution_constraints,
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


def test_datetime_fixture_is_timezone_aware() -> None:
    assert datetime(2026, 7, 31, tzinfo=UTC).utcoffset() is not None
