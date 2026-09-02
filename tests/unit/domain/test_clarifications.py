"""Interactive clarification contract validation and serialization."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from copilot.contracts import (
    ClarificationInputType,
    ClarificationQuestion,
    ClarificationResponse,
    ClarificationStatus,
    JsonObject,
    TaskClarification,
    TaskStatus,
)

NOW = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)


def test_waiting_clarification_and_pending_round_serialize_stably() -> None:
    question = ClarificationQuestion(
        field="legal_entity_ids",
        reason="An authorized legal entity is required.",
        prompt="Which authorized legal entity should be analyzed?",
        input_type=ClarificationInputType.SINGLE_SELECT,
        allowed_values=("LE-CN-01", "LE-DE-01"),
    )
    clarification = TaskClarification(
        clarification_id="CLAR-001",
        task_id="TASK-001",
        tenant_id="TENANT-001",
        round=1,
        status=ClarificationStatus.PENDING,
        questions=(question,),
        created_at=NOW,
    )

    assert clarification.model_dump(mode="json")["status"] == "PENDING"
    assert TaskStatus.WAITING_CLARIFICATION.value == "WAITING_CLARIFICATION"
    assert TaskClarification.model_validate_json(clarification.model_dump_json()) == clarification


def test_question_rejects_invalid_select_contracts() -> None:
    with pytest.raises(ValidationError, match="allowed_values"):
        ClarificationQuestion(
            field="legal_entity_ids",
            reason="Scope is required.",
            prompt="Choose an entity.",
            input_type=ClarificationInputType.SINGLE_SELECT,
        )
    with pytest.raises(ValidationError, match="unique"):
        ClarificationQuestion(
            field="legal_entity_ids",
            reason="Scope is required.",
            prompt="Choose an entity.",
            input_type=ClarificationInputType.SINGLE_SELECT,
            allowed_values=("LE-CN-01", "LE-CN-01"),
        )


def test_response_accepts_structured_or_natural_language_but_not_empty() -> None:
    assert (
        ClarificationResponse(
            answers=JsonObject(
                {
                    "time_range": {
                        "start_date": "2026-08-01",
                        "end_date": "2026-08-31",
                    }
                }
            )
        ).message
        is None
    )
    assert ClarificationResponse(message="  Use LE-CN-01.  ").message == "Use LE-CN-01."
    with pytest.raises(ValidationError, match="requires answers or message"):
        ClarificationResponse()


def test_round_and_lifecycle_fields_are_consistent() -> None:
    question = ClarificationQuestion(
        field="period",
        reason="A period is required.",
        prompt="Which period?",
        input_type=ClarificationInputType.TEXT,
    )
    with pytest.raises(ValidationError):
        TaskClarification(
            clarification_id="CLAR-001",
            task_id="TASK-001",
            tenant_id="TENANT-001",
            round=0,
            status=ClarificationStatus.PENDING,
            questions=(question,),
            created_at=NOW,
        )
    with pytest.raises(ValidationError, match="recorded together"):
        TaskClarification(
            clarification_id="CLAR-001",
            task_id="TASK-001",
            tenant_id="TENANT-001",
            round=1,
            status=ClarificationStatus.SUBMITTED,
            questions=(question,),
            response=ClarificationResponse(message="Q2 2026"),
            created_at=NOW,
        )
