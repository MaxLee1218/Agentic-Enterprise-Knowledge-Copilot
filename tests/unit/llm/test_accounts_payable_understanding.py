"""Stage 8 AP understanding adapter trust-boundary coverage."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from copilot.contracts import (
    AccountsPayableConstraintsV1,
    ArtifactType,
    MoneyThreshold,
    TaskType,
)
from copilot.llm.offline_mock import OfflineMockLLM
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
