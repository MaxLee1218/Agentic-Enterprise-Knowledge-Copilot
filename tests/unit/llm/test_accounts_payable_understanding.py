"""Stage 8 AP understanding adapter trust-boundary coverage."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from copilot.contracts import (
    AccountsPayableConstraintsV1,
    ArtifactType,
    ClarificationKind,
    ClarificationResponse,
    MoneyThreshold,
    TaskType,
)
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.llm.schemas import APTaskUnderstandingOutput, ClarificationAssistantOutput
from copilot.services.llm import LLMSchemaValidationError
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TaskOutputFormat,
    TrustedCallerContext,
)
from copilot.services.workflows.planning import TaskUnderstandingOutcome
from tests.workflow_helpers import build_test_container

_MANIFEST_CHECKSUM = "sha256:3095ebb099a2db12dffbc699cf1f65bb7d8e324d025eb701af4bf825d6adab33"


def _caller() -> TrustedCallerContext:
    return TrustedCallerContext(
        user_id="U-FINANCE-001",
        tenant_id="TENANT-DEMO",
        data_scope=("accounts_payable.v1", "accounts-payable-policy-v1"),
        legal_entity_ids=("LE-CN-01", "LE-US-01"),
        business_unit_ids=("BU-CN-01", "BU-US-01"),
        currency_scope=("CNY", "USD"),
        allowed_task_types=(TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1,),
        roles=("finance_analyst",),
        scopes=("task:execute", "finance:ap.detail"),
        purpose=TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1.value,
        policy_rule_set_id="accounts-payable-v1",
        policy_rule_set_version="ap_rules.2026.1",
        policy_manifest_checksum=_MANIFEST_CHECKSUM,
        policy_materiality=(
            MoneyThreshold(currency="CNY", amount=Decimal("5000")),
            MoneyThreshold(currency="USD", amount=Decimal("1000")),
        ),
        policy_snapshot_at=datetime(2026, 7, 1, tzinfo=UTC),
    )


def _understand(tmp_path: Path, text: str) -> TaskUnderstandingOutcome:
    with build_test_container(
        tmp_path / "artifacts",
        llm_provider=OfflineMockLLM(),
    ) as container:
        request, context = container.task_service.prepare(
            NaturalLanguageTaskCommand(
                task=text,
                output_format=TaskOutputFormat.JSON,
                source=RequestSource.INTERNAL,
            ),
            _caller(),
        )
        assert container.planning_service is not None
        return container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
        )


def test_explicit_ap_dates_are_merged_with_trusted_policy_and_scope(tmp_path: Path) -> None:
    outcome = _understand(
        tmp_path,
        "Analyze duplicate and late payment exceptions from 2026-04-01 to 2026-06-30 "
        "for LE-US-01 in USD",
    )

    assert outcome.contract is not None
    assert outcome.contract.task_type is TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1
    assert (
        outcome.contract.expected_output.artifact_type is ArtifactType.ACCOUNTS_PAYABLE_REPORT_JSON
    )
    constraints = outcome.contract.constraints
    assert isinstance(constraints, AccountsPayableConstraintsV1)
    assert constraints.legal_entity_ids == ("LE-US-01",)
    assert constraints.currency_scope == ("USD",)
    assert constraints.policy_manifest_checksum == _MANIFEST_CHECKSUM
    assert constraints.effective_materiality == (
        MoneyThreshold(currency="USD", amount=Decimal("1000")),
    )
    assert (constraints.deadline_at - outcome.contract.created_at).total_seconds() <= 180


def test_missing_ap_date_range_remains_missing_information(tmp_path: Path) -> None:
    outcome = _understand(tmp_path, "Analyze Accounts Payable exceptions for LE-US-01")

    assert outcome.contract is None
    assert any("date range" in item for item in outcome.missing_information)


def test_period_after_snapshot_requests_a_corrected_range(tmp_path: Path) -> None:
    outcome = _understand(tmp_path, "Analyze Accounts Payable for 2026 CN")

    assert outcome.contract is None
    assert outcome.clarification_context.values.root == {"legal_entity_ids": ["LE-CN-01"]}
    question = next(item for item in outcome.questions if item.field == "time_range")
    assert "2026-12-31" in question.prompt
    assert "2026-07-01" in question.prompt
    assert "What exact period" in question.prompt


def test_model_candidate_handles_abbreviated_end_date_with_confirmation(
    tmp_path: Path,
) -> None:
    with build_test_container(
        tmp_path / "artifacts",
        llm_provider=OfflineMockLLM(),
    ) as container:
        request, context = container.task_service.prepare(
            NaturalLanguageTaskCommand(
                task="Analyze Accounts Payable for 2026 CN",
                output_format=TaskOutputFormat.JSON,
                source=RequestSource.INTERNAL,
            ),
            _caller(),
        )
        assert container.planning_service is not None
        first = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
        )
        confirmation = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
            clarification_context=first.clarification_context,
            clarification_response=ClarificationResponse(
                message="from New Year's Day through July first of twenty twenty-six"
            ),
        )
        resolved = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
            clarification_context=confirmation.clarification_context,
            clarification_response=ClarificationResponse(message="yes"),
        )

    assert confirmation.clarification_kind is ClarificationKind.CANDIDATE_CONFIRMATION
    assert confirmation.candidate_interpretation is not None
    assert "2026-01-01 through 2026-07-01" in (confirmation.candidate_interpretation.display_text)
    assert confirmation.assistant_message is not None
    assert "2026-01-01 through 2026-07-01" in confirmation.assistant_message
    assert resolved.contract is not None
    constraints = resolved.contract.constraints
    assert isinstance(constraints, AccountsPayableConstraintsV1)
    assert constraints.time_range.start_date.isoformat() == "2026-01-01"
    assert constraints.time_range.end_date.isoformat() == "2026-07-01"


def test_dotted_numeric_clarification_resolves_without_repeating_the_year(
    tmp_path: Path,
) -> None:
    with build_test_container(
        tmp_path / "artifacts",
        llm_provider=OfflineMockLLM(),
    ) as container:
        request, context = container.task_service.prepare(
            NaturalLanguageTaskCommand(
                task="Analyze Accounts Payable for 2026 CN",
                output_format=TaskOutputFormat.JSON,
                source=RequestSource.INTERNAL,
            ),
            _caller(),
        )
        assert container.planning_service is not None
        first = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
        )
        resolved = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
            clarification_context=first.clarification_context,
            clarification_response=ClarificationResponse(message="2026 1.1-7.1"),
        )

    assert resolved.contract is not None
    constraints = resolved.contract.constraints
    assert isinstance(constraints, AccountsPayableConstraintsV1)
    assert constraints.time_range.start_date.isoformat() == "2026-01-01"
    assert constraints.time_range.end_date.isoformat() == "2026-07-01"


def test_model_entity_candidate_requires_confirmation_inside_authorized_scope(
    tmp_path: Path,
) -> None:
    with build_test_container(
        tmp_path / "artifacts",
        llm_provider=OfflineMockLLM(),
    ) as container:
        request, context = container.task_service.prepare(
            NaturalLanguageTaskCommand(
                task=(
                    "Analyze Accounts Payable from 2026-04-01 to 2026-06-30 for our PRC operation"
                ),
                output_format=TaskOutputFormat.JSON,
                source=RequestSource.INTERNAL,
            ),
            _caller(),
        )
        assert container.planning_service is not None
        confirmation = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
        )
        resolved = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
            clarification_context=confirmation.clarification_context,
            clarification_response=ClarificationResponse(message="yes"),
        )

    assert confirmation.clarification_kind is ClarificationKind.CANDIDATE_CONFIRMATION
    assert confirmation.candidate_interpretation is not None
    assert "legal entity LE-CN-01" in confirmation.candidate_interpretation.display_text
    assert resolved.contract is not None
    constraints = resolved.contract.constraints
    assert isinstance(constraints, AccountsPayableConstraintsV1)
    assert constraints.legal_entity_ids == ("LE-CN-01",)


def test_model_entity_candidate_without_a_user_anchor_is_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OfflineMockLLM()
    generate_structured = provider.generate_structured

    def hallucinate_entity(**kwargs: Any) -> Any:
        result = generate_structured(**kwargs)
        if kwargs["output_schema"] is APTaskUnderstandingOutput:
            candidate = result.parsed_output
            assert isinstance(candidate, APTaskUnderstandingOutput)
            return result.model_copy(
                update={
                    "parsed_output": candidate.model_copy(
                        update={"requested_legal_entity_ids": ("LE-CN-01",)}
                    )
                }
            )
        return result

    monkeypatch.setattr(provider, "generate_structured", hallucinate_entity)
    with build_test_container(
        tmp_path / "artifacts",
        llm_provider=provider,
    ) as container:
        request, context = container.task_service.prepare(
            NaturalLanguageTaskCommand(
                task="Analyze Accounts Payable from 2026-04-01 to 2026-06-30",
                output_format=TaskOutputFormat.JSON,
                source=RequestSource.INTERNAL,
            ),
            _caller(),
        )
        assert container.planning_service is not None
        outcome = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
        )

    assert outcome.contract is None
    assert outcome.candidate_interpretation is None
    assert [question.field for question in outcome.questions] == ["legal_entity_ids"]


def test_clarification_copy_falls_back_when_the_presentation_call_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OfflineMockLLM()
    generate_structured = provider.generate_structured

    def fail_only_presentation(**kwargs: Any) -> Any:
        if kwargs["output_schema"] is ClarificationAssistantOutput:
            raise LLMSchemaValidationError("invalid clarification response")
        return generate_structured(**kwargs)

    monkeypatch.setattr(provider, "generate_structured", fail_only_presentation)
    with build_test_container(
        tmp_path / "artifacts",
        llm_provider=provider,
    ) as container:
        request, context = container.task_service.prepare(
            NaturalLanguageTaskCommand(
                task="Analyze Accounts Payable exceptions for LE-US-01",
                output_format=TaskOutputFormat.JSON,
                source=RequestSource.INTERNAL,
            ),
            _caller(),
        )
        assert container.planning_service is not None
        outcome = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
        )

    assert outcome.contract is None
    assert outcome.assistant_message is None
    assert [question.field for question in outcome.questions] == ["time_range"]


def test_natural_clarification_resolves_full_year_and_unique_country_code(
    tmp_path: Path,
) -> None:
    with build_test_container(
        tmp_path / "artifacts",
        llm_provider=OfflineMockLLM(),
    ) as container:
        request, context = container.task_service.prepare(
            NaturalLanguageTaskCommand(
                task="analyse Accounts Payable compliance recently",
                output_format=TaskOutputFormat.JSON,
                source=RequestSource.INTERNAL,
            ),
            _caller(),
        )
        assert container.planning_service is not None
        first = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
        )
        resolved = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
            clarification_context=first.clarification_context,
            clarification_response=ClarificationResponse(message="year2025 CN"),
        )

    assert {item.field for item in first.questions} == {"time_range", "legal_entity_ids"}
    assert resolved.contract is not None
    constraints = resolved.contract.constraints
    assert isinstance(constraints, AccountsPayableConstraintsV1)
    assert constraints.time_range.start_date.isoformat() == "2025-01-01"
    assert constraints.time_range.end_date.isoformat() == "2025-12-31"
    assert constraints.legal_entity_ids == ("LE-CN-01",)
    assert request.raw_input == "analyse Accounts Payable compliance recently"


def test_partial_natural_answer_retains_date_and_only_reasks_entity(tmp_path: Path) -> None:
    with build_test_container(
        tmp_path / "artifacts",
        llm_provider=OfflineMockLLM(),
    ) as container:
        request, context = container.task_service.prepare(
            NaturalLanguageTaskCommand(
                task="analyse Accounts Payable compliance recently",
                output_format=TaskOutputFormat.JSON,
                source=RequestSource.INTERNAL,
            ),
            _caller(),
        )
        assert container.planning_service is not None
        first = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
        )
        partial = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
            clarification_context=first.clarification_context,
            clarification_response=ClarificationResponse(message="2025"),
        )

    assert [item.field for item in partial.questions] == ["legal_entity_ids"]
    assert partial.clarification_context.values.root["start_date"] == "2025-01-01"


def test_ambiguous_country_alias_retains_date_and_lists_only_matching_entities(
    tmp_path: Path,
) -> None:
    caller = _caller().model_copy(update={"legal_entity_ids": ("LE-CN-01", "LE-CN-02", "LE-US-01")})
    with build_test_container(
        tmp_path / "artifacts",
        llm_provider=OfflineMockLLM(),
    ) as container:
        request, context = container.task_service.prepare(
            NaturalLanguageTaskCommand(
                task="analyse Accounts Payable compliance recently",
                output_format=TaskOutputFormat.JSON,
                source=RequestSource.INTERNAL,
            ),
            caller,
        )
        assert container.planning_service is not None
        first = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
        )
        ambiguous = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
            clarification_context=first.clarification_context,
            clarification_response=ClarificationResponse(message="2025 CN"),
        )

    assert ambiguous.clarification_kind is ClarificationKind.AMBIGUITY_RESOLUTION
    assert [item.field for item in ambiguous.questions] == ["legal_entity_ids"]
    assert ambiguous.questions[0].allowed_values == ("LE-CN-01", "LE-CN-02")


def test_named_entity_confirmation_accepts_yes_against_persistable_candidate(
    tmp_path: Path,
) -> None:
    with build_test_container(
        tmp_path / "artifacts",
        llm_provider=OfflineMockLLM(),
    ) as container:
        request, context = container.task_service.prepare(
            NaturalLanguageTaskCommand(
                task="analyse Accounts Payable compliance recently",
                output_format=TaskOutputFormat.JSON,
                source=RequestSource.INTERNAL,
            ),
            _caller(),
        )
        assert container.planning_service is not None
        first = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
        )
        confirmation = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
            clarification_context=first.clarification_context,
            clarification_response=ClarificationResponse(message="year 2025 China"),
        )
        resolved = container.planning_service.understand(
            request=request,
            trusted_context=context,
            trace_id=context.trace_id,
            max_steps=14,
            clarification_context=confirmation.clarification_context,
            clarification_response=ClarificationResponse(message="yes, continue"),
        )

    assert confirmation.clarification_kind is ClarificationKind.CANDIDATE_CONFIRMATION
    assert confirmation.candidate_interpretation is not None
    assert resolved.contract is not None
    constraints = resolved.contract.constraints
    assert isinstance(constraints, AccountsPayableConstraintsV1)
    assert constraints.legal_entity_ids == ("LE-CN-01",)


@pytest.mark.parametrize(
    "text",
    (
        "Analyze AP from 2026-04-01 to 2026-06-30 for LE-UNAUTHORIZED",
        "Analyze AP from 2026-04-01 to 2026-06-30 for LE-US-01 with USD materiality 5000",
    ),
)
def test_scope_or_policy_relaxation_candidate_is_rejected(
    tmp_path: Path,
    text: str,
) -> None:
    with pytest.raises(LLMSchemaValidationError):
        _understand(tmp_path, text)
