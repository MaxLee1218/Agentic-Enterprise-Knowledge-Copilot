"""Deterministic deliverable, citation, numeric, safety, and composite tests."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from typing import TypedDict

import pytest

from copilot.contracts import (
    ApprovalRequest,
    ApprovalRequirement,
    ApprovalResolutionAction,
    ApprovalStatus,
    CandidateResult,
    CitationClaim,
    ClaimType,
    DeliverableRecord,
    ErrorType,
    EvidenceType,
    JsonObject,
    NumericClaim,
    StepResult,
    StepResultStatus,
    TaskContract,
    TaskError,
    TaskPlan,
    VerificationContext,
    VerificationIssue,
    VerificationSeverity,
    VerificationStatus,
)
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.evidence.validators import (
    CitationVerifier,
    CompositeVerifier,
    DeliverableVerifier,
    EvidenceLedgerView,
    NumericVerifier,
    SafetyVerifier,
)
from copilot.policies.approval import action_fingerprint, schema_fingerprint
from tests.unit.evidence.helpers import (
    CALC_ID,
    DB_ID,
    DOC_ID,
    NOW,
    TASK_ID,
    evidence_item,
    valid_candidate,
    valid_contract,
    valid_ledger,
    valid_plan,
    valid_step_results,
    valid_verification_context,
)


class VerifierArguments(TypedDict):
    """Keyword arguments shared by the verifier interfaces."""

    task_contract: TaskContract
    task_plan: TaskPlan
    step_results: Mapping[str, StepResult]
    evidence_ledger: EvidenceLedgerView
    verification_context: VerificationContext
    candidate_result: CandidateResult


def verifier_arguments() -> VerifierArguments:
    """Return shared valid verifier inputs."""
    return {
        "task_contract": valid_contract(),
        "task_plan": valid_plan(),
        "step_results": valid_step_results(),
        "evidence_ledger": valid_ledger(),
        "verification_context": valid_verification_context(),
        "candidate_result": valid_candidate(),
    }


def issue_codes(issues: tuple[VerificationIssue, ...]) -> set[str]:
    """Return stable codes from a verifier result."""
    return {issue.code for issue in issues}


def test_deliverables_pass_with_extra_output() -> None:
    arguments = verifier_arguments()
    candidate = valid_candidate()
    arguments["candidate_result"] = candidate.model_copy(
        update={
            "deliverables": (
                *candidate.deliverables,
                DeliverableRecord(
                    deliverable_id="extra",
                    producing_step_id="S-RP",
                    content={"value": True},
                ),
            )
        }
    )

    assert DeliverableVerifier().verify(**arguments) == ()


@pytest.mark.parametrize(
    ("candidate", "step_results", "expected_code"),
    [
        (
            valid_candidate().model_copy(
                update={"deliverables": valid_candidate().deliverables[:-1]}
            ),
            valid_step_results(),
            "DELIVERABLE_MISSING",
        ),
        (
            valid_candidate().model_copy(
                update={
                    "deliverables": (
                        valid_candidate().deliverables[0].model_copy(update={"content": {}}),
                        *valid_candidate().deliverables[1:],
                    )
                }
            ),
            valid_step_results(),
            "DELIVERABLE_CONTENT_EMPTY",
        ),
        (
            valid_candidate(),
            {
                **valid_step_results(),
                "S-RP": StepResult(
                    step_id="S-RP",
                    status=StepResultStatus.CANCELLED,
                    output=None,
                    evidence=(),
                    error=TaskError(
                        error_code="TEST_CANCELLED",
                        error_type=ErrorType.CANCELLATION,
                        message="Report step was skipped",
                        recoverable=False,
                    ),
                ),
            },
            "DELIVERABLE_STEP_NOT_SUCCESSFUL",
        ),
    ],
)
def test_deliverable_failures(
    candidate: CandidateResult,
    step_results: dict[str, StepResult],
    expected_code: str,
) -> None:
    arguments = verifier_arguments()
    arguments["candidate_result"] = candidate
    arguments["step_results"] = step_results

    issues = DeliverableVerifier().verify(**arguments)
    assert expected_code in issue_codes(issues)


def test_deliverable_invalid_evidence_reference_fails() -> None:
    arguments = verifier_arguments()
    candidate = valid_candidate()
    first = candidate.deliverables[0].model_copy(update={"evidence_ids": ("E-MISSING",)})
    arguments["candidate_result"] = candidate.model_copy(
        update={"deliverables": (first, *candidate.deliverables[1:])}
    )

    issues = DeliverableVerifier().verify(**arguments)
    assert "DELIVERABLE_EVIDENCE_INVALID" in issue_codes(issues)


def test_valid_structured_citations_pass() -> None:
    assert CitationVerifier().verify(**verifier_arguments()) == ()


@pytest.mark.parametrize(
    ("claim", "expected_code"),
    [
        (
            CitationClaim(
                claim_id="C-MISSING",
                claim_type=ClaimType.DATA,
                evidence_ids=("E-MISSING",),
            ),
            "CITATION_REFERENCE_INVALID",
        ),
        (
            CitationClaim(
                claim_id="C-EMPTY",
                claim_type=ClaimType.DATA,
                evidence_ids=(),
            ),
            "CITATION_REQUIRED",
        ),
        (
            CitationClaim(
                claim_id="C-WRONG",
                claim_type=ClaimType.POLICY,
                evidence_ids=(DB_ID,),
            ),
            "CITATION_TYPE_INCOMPATIBLE",
        ),
        (
            CitationClaim(
                claim_id="C-NO-DB",
                claim_type=ClaimType.DATA,
                evidence_ids=(DOC_ID,),
            ),
            "DATA_CLAIM_DATABASE_LINEAGE_MISSING",
        ),
    ],
)
def test_invalid_citations_fail(claim: CitationClaim, expected_code: str) -> None:
    arguments = verifier_arguments()
    arguments["candidate_result"] = valid_candidate().model_copy(update={"claims": (claim,)})

    issues = CitationVerifier().verify(**arguments)
    assert expected_code in issue_codes(issues)


def test_document_without_source_and_database_without_query_id_fail() -> None:
    ledger = InMemoryEvidenceLedger()
    ledger.add(
        evidence_item(
            "E-DOC-BAD",
            EvidenceType.DOCUMENT,
            reference={"document_id": "DOC-1"},
        ),
        tenant_id="TENANT-A",
    )
    ledger.add(
        evidence_item(
            "E-DB-BAD",
            EvidenceType.DATABASE,
            reference={"query_template_id": "supplier_quality_summary_v1"},
        ),
        tenant_id="TENANT-A",
    )
    arguments = verifier_arguments()
    arguments["evidence_ledger"] = ledger
    arguments["candidate_result"] = valid_candidate().model_copy(
        update={
            "claims": (
                CitationClaim(
                    claim_id="C-DOC-BAD",
                    claim_type=ClaimType.POLICY,
                    evidence_ids=("E-DOC-BAD",),
                ),
                CitationClaim(
                    claim_id="C-DB-BAD",
                    claim_type=ClaimType.DATA,
                    evidence_ids=("E-DB-BAD",),
                ),
            )
        }
    )

    codes = issue_codes(CitationVerifier().verify(**arguments))
    assert {"DOCUMENT_SOURCE_INVALID", "DATABASE_QUERY_ID_MISSING"}.issubset(codes)


@pytest.mark.parametrize(
    ("claim", "expected_code"),
    [
        (
            NumericClaim(
                claim_id="C-COUNT-BAD",
                metric_name="defect_count",
                value=16,
                unit="count",
                precision=0,
                evidence_ids=(CALC_ID,),
                dimensions=JsonObject({"scope": "all_suppliers"}),
            ),
            "NUMERIC_CLAIM_MISMATCH",
        ),
        (
            NumericClaim(
                claim_id="C-RATE-BAD",
                metric_name="defect_rate",
                value=Decimal("0.0200"),
                unit="ratio",
                precision=4,
                evidence_ids=(CALC_ID,),
                dimensions=JsonObject({"scope": "all_suppliers"}),
            ),
            "NUMERIC_CLAIM_MISMATCH",
        ),
        (
            NumericClaim(
                claim_id="C-UNIT-BAD",
                metric_name="defect_rate",
                value=Decimal("1.5000"),
                unit="percent",
                precision=4,
                evidence_ids=(CALC_ID,),
                dimensions=JsonObject({"scope": "all_suppliers"}),
            ),
            "NUMERIC_UNIT_MISMATCH",
        ),
        (
            NumericClaim(
                claim_id="C-EVIDENCE-BAD",
                metric_name="defect_rate",
                value=Decimal("0.0150"),
                unit="ratio",
                precision=4,
                evidence_ids=(DB_ID,),
                dimensions=JsonObject({"scope": "all_suppliers"}),
            ),
            "NUMERIC_BASELINE_NOT_UNIQUE",
        ),
    ],
)
def test_numeric_mismatches_are_errors(claim: NumericClaim, expected_code: str) -> None:
    arguments = verifier_arguments()
    arguments["candidate_result"] = valid_candidate().model_copy(
        update={"numeric_claims": (claim,)}
    )

    issues = NumericVerifier().verify(**arguments)
    assert expected_code in issue_codes(issues)
    assert all(issue.severity is VerificationSeverity.ERROR for issue in issues)


def test_numeric_formatting_and_legal_rounding_difference_pass() -> None:
    arguments = verifier_arguments()
    exact_format = (
        valid_candidate().numeric_claims[0].model_copy(update={"value": Decimal("0.01500")})
    )
    arguments["candidate_result"] = valid_candidate().model_copy(
        update={"numeric_claims": (exact_format,)}
    )
    assert NumericVerifier().verify(**arguments) == ()


def test_non_finite_numeric_claim_is_rejected_by_contract() -> None:
    with pytest.raises(ValueError, match="finite"):
        NumericClaim(
            claim_id="C-NAN",
            metric_name="defect_rate",
            value=Decimal("NaN"),
            unit="ratio",
            precision=4,
            evidence_ids=(CALC_ID,),
        )


def test_safety_valid_readonly_execution_passes() -> None:
    assert SafetyVerifier(clock=lambda: NOW).verify(**verifier_arguments()) == ()


def test_safety_detects_unregistered_and_unplanned_execution() -> None:
    arguments = verifier_arguments()
    context = valid_verification_context()
    rogue_call = context.tool_calls[0].model_copy(
        update={"tool_name": "rogue_tool", "step_id": "S-ROGUE"}
    )
    rogue_result = context.tool_results[0].model_copy(
        update={"tool_name": "rogue_tool", "step_id": "S-ROGUE"}
    )
    arguments["verification_context"] = context.model_copy(
        update={"tool_calls": (rogue_call,), "tool_results": (rogue_result,)}
    )

    codes = issue_codes(SafetyVerifier(clock=lambda: NOW).verify(**arguments))
    assert {"TOOL_NOT_REGISTERED", "TOOL_NOT_IN_PLAN", "READONLY_TASK_WRITE_TOOL"}.issubset(codes)


def test_safety_detects_missing_approval_and_parameter_mismatch() -> None:
    arguments = verifier_arguments()
    contract = valid_contract().model_copy(
        update={
            "approval_requirement": ApprovalRequirement(
                required=True,
                policy_id="quality-policy",
                approver_role="quality_data_approver",
                controlled_scope=("quality.v1",),
            )
        }
    )
    arguments["task_contract"] = contract
    assert "APPROVAL_REQUIRED" in issue_codes(SafetyVerifier(clock=lambda: NOW).verify(**arguments))


@pytest.mark.parametrize(
    ("status", "fingerprint_valid", "expires_delta"),
    [
        (ApprovalStatus.REJECTED, True, timedelta(hours=1)),
        (ApprovalStatus.APPROVED, False, timedelta(hours=1)),
        (ApprovalStatus.APPROVED, True, timedelta(minutes=-1)),
    ],
)
def test_rejected_mismatched_and_expired_approvals_fail(
    status: ApprovalStatus,
    fingerprint_valid: bool,
    expires_delta: timedelta,
) -> None:
    arguments = verifier_arguments()
    contract = valid_contract().model_copy(
        update={
            "approval_requirement": ApprovalRequirement(
                required=True,
                policy_id="quality-policy",
                approver_role="quality_data_approver",
                controlled_scope=("quality.v1",),
            )
        }
    )
    base = valid_verification_context()
    call = base.tool_calls[0].model_copy(update={"approval_id": "AP-001"})
    definition = base.registered_tools[0]
    schema_digest = schema_fingerprint(definition)
    expected_fingerprint = action_fingerprint(
        task_id=TASK_ID,
        planning_version=1,
        step_id=call.step_id,
        tool_name=call.tool_name,
        tool_version=call.tool_version,
        input_schema_fingerprint=schema_digest,
        controlled_scope=("quality.v1",),
        arguments=call.input,
    )
    fingerprint = expected_fingerprint if fingerprint_valid else "wrong-action"
    expires_at = NOW + expires_delta
    created_at = min(NOW - timedelta(hours=1), expires_at - timedelta(hours=1))
    approval = ApprovalRequest(
        approval_id="AP-001",
        task_id=TASK_ID,
        tenant_id="TENANT-A",
        step_id=call.step_id,
        planning_version=1,
        tool_name=call.tool_name,
        tool_version=call.tool_version,
        input_schema_fingerprint=schema_digest,
        original_action_fingerprint=fingerprint,
        resolved_action_fingerprint=(fingerprint if status is ApprovalStatus.APPROVED else None),
        controlled_scope=("quality.v1",),
        proposed_arguments=call.input,
        resolved_arguments=call.input if status is ApprovalStatus.APPROVED else None,
        reason="Controlled supplier quality data access",
        requester="U-001",
        approver="A-001",
        required_role="quality_data_approver",
        status=status,
        resolution_action=(
            ApprovalResolutionAction.APPROVE
            if status is ApprovalStatus.APPROVED
            else ApprovalResolutionAction.REJECT
        ),
        resolution_reason=("Rejected for test" if status is ApprovalStatus.REJECTED else None),
        policy_version="quality-policy.v1",
        created_at=created_at,
        decided_at=created_at + timedelta(minutes=1),
        expires_at=expires_at,
    )
    arguments["task_contract"] = contract
    arguments["verification_context"] = base.model_copy(
        update={
            "tool_calls": (call,),
            "tool_results": (base.tool_results[0],),
            "approvals": (approval,),
        }
    )

    codes = issue_codes(SafetyVerifier(clock=lambda: NOW).verify(**arguments))
    assert "APPROVAL_SCOPE_INVALID" in codes


def test_safety_detects_unauthorized_table_and_sensitive_output() -> None:
    ledger = InMemoryEvidenceLedger()
    ledger.add(
        evidence_item(
            DB_ID,
            EvidenceType.DATABASE,
            step_id="S-DB",
            reference={
                "query_fingerprint": "sha256:query",
                "table_names": ["payroll"],
                "column_names": ["payroll.secret_value"],
                "statement_type": "SELECT",
                "read_only": True,
            },
        ),
        tenant_id="TENANT-A",
    )
    arguments = verifier_arguments()
    arguments["evidence_ledger"] = ledger
    arguments["candidate_result"] = valid_candidate().model_copy(
        update={"output_fields": ("secret_value",)}
    )
    context = valid_verification_context().model_copy(
        update={"sensitive_fields": ("payroll.secret_value", "secret_value")}
    )
    arguments["verification_context"] = context

    codes = issue_codes(SafetyVerifier(clock=lambda: NOW).verify(**arguments))
    expected = {
        "DATABASE_TABLE_NOT_ALLOWED",
        "DATABASE_SENSITIVE_FIELD",
        "SENSITIVE_FIELD_OUTPUT",
    }
    assert expected.issubset(codes)


def test_safety_rejects_database_write_metadata_as_error() -> None:
    ledger = InMemoryEvidenceLedger()
    ledger.add(
        evidence_item(
            DB_ID,
            EvidenceType.DATABASE,
            step_id="S-DB",
            reference={
                "query_fingerprint": "sha256:query",
                "table_names": ["incoming_inspections"],
                "column_names": ["incoming_inspections.total_quantity"],
                "statement_type": "UPDATE",
                "read_only": False,
            },
        ),
        tenant_id="TENANT-A",
    )
    arguments = verifier_arguments()
    arguments["evidence_ledger"] = ledger

    issues = SafetyVerifier(clock=lambda: NOW).verify(**arguments)
    write_issue = next(
        issue for issue in issues if issue.code == "DATABASE_READONLY_METADATA_INVALID"
    )
    assert write_issue.severity is VerificationSeverity.ERROR


class WarningVerifier:
    """Controlled warning-only verifier used to fix aggregation behavior."""

    name = "WarningVerifier"

    def verify(
        self,
        *,
        task_contract: TaskContract,
        task_plan: TaskPlan,
        step_results: Mapping[str, StepResult],
        evidence_ledger: EvidenceLedgerView,
        verification_context: VerificationContext,
        candidate_result: CandidateResult,
    ) -> tuple[VerificationIssue, ...]:
        del task_plan, step_results, evidence_ledger, verification_context, candidate_result
        return (
            VerificationIssue(
                code="CONTROLLED_WARNING",
                message="Controlled warning",
                severity=VerificationSeverity.WARNING,
                verifier=self.name,
                task_id=task_contract.task_id,
            ),
        )


def test_composite_status_counts_order_and_serialization() -> None:
    passed = CompositeVerifier(clock=lambda: NOW, timer=lambda: 1.0).verify(**verifier_arguments())
    assert passed.status is VerificationStatus.PASSED
    assert passed.error_count == passed.warning_count == 0
    assert passed.model_dump_json()

    warning = CompositeVerifier(
        verifiers=(WarningVerifier(),),
        clock=lambda: NOW,
        timer=lambda: 1.0,
    ).verify(**verifier_arguments())
    assert warning.status is VerificationStatus.PASSED_WITH_WARNINGS
    assert warning.warning_count == 1

    arguments = verifier_arguments()
    candidate = valid_candidate()
    arguments["candidate_result"] = candidate.model_copy(
        update={
            "deliverables": candidate.deliverables[:-1],
            "numeric_claims": (
                candidate.numeric_claims[0].model_copy(update={"value": Decimal("0.5")}),
            ),
            "output_fields": ("secret_value",),
        }
    )
    failed = CompositeVerifier(clock=lambda: NOW, timer=lambda: 1.0).verify(**arguments)
    assert failed.status is VerificationStatus.FAILED
    assert failed.error_count >= 3
    assert "SENSITIVE_FIELD_OUTPUT" in [issue.code for issue in failed.issues]
    assert [check.verifier for check in failed.checks][-1] == "SafetyVerifier"


def test_approval_expiration_is_not_treated_as_valid() -> None:
    arguments = verifier_arguments()
    contract = valid_contract().model_copy(
        update={
            "approval_requirement": ApprovalRequirement(
                required=True,
                policy_id="quality-policy",
                approver_role="quality_data_approver",
                controlled_scope=("quality.v1",),
            )
        }
    )
    context = valid_verification_context()
    arguments["task_contract"] = contract
    arguments["verification_context"] = context.model_copy(
        update={"approvals": (), "tool_calls": context.tool_calls}
    )
    future = NOW + timedelta(days=1)

    codes = issue_codes(SafetyVerifier(clock=lambda: future).verify(**arguments))
    assert "APPROVAL_REQUIRED" in codes
