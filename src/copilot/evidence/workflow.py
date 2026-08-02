"""Infrastructure adapter for workflow-level deterministic Artifact verification."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import cast

from copilot.contracts import (
    CandidateResult,
    JsonObject,
    StepResultStatus,
    StepType,
    ToolDefinition,
    VerificationCheck,
    VerificationContext,
    VerificationIssue,
    VerificationResult,
    VerificationSeverity,
    VerificationStatus,
)
from copilot.contracts.validators import utc_now
from copilot.evidence.citations import candidate_from_json_report
from copilot.evidence.validators import CompositeVerifier, EvidenceLedgerView
from copilot.services.workflows.models import WorkflowExecutionContext
from copilot.services.workflows.ports import ArtifactStore
from copilot.tools.reporting.validator import report_mapping_from_bytes


class WorkflowVerifier:
    """Adapt the current structured JSON Artifact into the reusable verifier service."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        evidence_ledger: EvidenceLedgerView,
        *,
        allowed_tables: tuple[str, ...],
        allowed_columns: tuple[str, ...],
        sensitive_fields: tuple[str, ...],
        composite: CompositeVerifier | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._artifact_store = artifact_store
        self._evidence_ledger = evidence_ledger
        self._allowed_tables = allowed_tables
        self._allowed_columns = allowed_columns
        self._sensitive_fields = sensitive_fields
        self._clock = clock
        self._composite = composite or CompositeVerifier(clock=clock)

    def verify(self, context: WorkflowExecutionContext) -> VerificationResult:
        """Return all safe verification issues instead of failing on the first issue."""
        artifact_issues, candidate = self._artifact_candidate(context)
        verification_context = VerificationContext(
            trace_id=context.task_id,
            registered_tools=tuple(
                # Registry ordering is supplied by the runner as a safe snapshot.
                cast(tuple[ToolDefinition, ...], context.metadata["registered_tools"])
            ),
            tool_calls=tuple(context.tool_calls),
            tool_results=tuple(
                result
                for step in context.plan.steps
                for result in context.tool_results.get(step.step_id, ())
            ),
            approvals=context.approvals,
            allowed_tables=self._allowed_tables,
            allowed_columns=self._allowed_columns,
            sensitive_fields=self._sensitive_fields,
            readonly_task=True,
        )
        result = self._composite.verify(
            task_contract=context.contract,
            task_plan=context.plan,
            step_results=context.step_results,
            evidence_ledger=self._evidence_ledger,
            verification_context=verification_context,
            candidate_result=candidate,
        )
        if any(
            step.status is not StepResultStatus.SUCCESS for step in context.step_results.values()
        ):
            artifact_issues = (
                _artifact_issue(
                    context.task_id,
                    "REQUIRED_STEP_NOT_SUCCESSFUL",
                    "Not every required plan step completed successfully",
                ),
                *artifact_issues,
            )
        artifact_check = VerificationCheck(
            verifier="ArtifactIntegrityVerifier",
            passed=not artifact_issues,
            issue_codes=tuple(issue.code for issue in artifact_issues),
            verified_evidence_ids=tuple(context.evidence),
        )
        issues = tuple(artifact_issues) + result.issues
        warning_count = sum(issue.severity is VerificationSeverity.WARNING for issue in issues)
        error_count = sum(issue.severity is VerificationSeverity.ERROR for issue in issues)
        status = (
            VerificationStatus.FAILED
            if error_count
            else (
                VerificationStatus.PASSED_WITH_WARNINGS
                if warning_count
                else VerificationStatus.PASSED
            )
        )
        return result.model_copy(
            update={
                "status": status,
                "issues": issues,
                "checks": (artifact_check, *result.checks),
                "warning_count": warning_count,
                "error_count": error_count,
            }
        )

    def _artifact_candidate(
        self,
        context: WorkflowExecutionContext,
    ) -> tuple[tuple[VerificationIssue, ...], CandidateResult]:
        issues: list[VerificationIssue] = []
        empty = CandidateResult(
            task_id=context.task_id,
            deliverables=(),
            claims=(),
            numeric_claims=(),
        )
        if len(context.artifacts) != 1:
            issues.append(
                _artifact_issue(
                    context.task_id,
                    "ARTIFACT_COUNT_INVALID",
                    "Exactly one final report artifact is required",
                )
            )
            return tuple(issues), empty
        artifact = context.artifacts[0]
        if artifact.task_id != context.task_id:
            issues.append(
                _artifact_issue(
                    context.task_id,
                    "ARTIFACT_CROSS_TASK",
                    "Report artifact belongs to another task",
                )
            )
        if artifact.type is not context.contract.expected_output.artifact_type:
            issues.append(
                _artifact_issue(
                    context.task_id,
                    "ARTIFACT_TYPE_MISMATCH",
                    "Report artifact type differs from the task contract",
                )
            )
        try:
            content = self._artifact_store.path_for(artifact).read_bytes()
        except (OSError, ValueError):
            issues.append(
                _artifact_issue(
                    context.task_id,
                    "ARTIFACT_UNREADABLE",
                    "Report artifact cannot be read from governed storage",
                )
            )
            return tuple(issues), empty
        if not content or len(content) != artifact.size_bytes:
            issues.append(
                _artifact_issue(
                    context.task_id,
                    "ARTIFACT_SIZE_INVALID",
                    "Report artifact is empty or its size metadata is invalid",
                )
            )
        checksum = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if checksum != artifact.checksum:
            issues.append(
                _artifact_issue(
                    context.task_id,
                    "ARTIFACT_CHECKSUM_MISMATCH",
                    "Report artifact checksum does not match committed metadata",
                )
            )
        if not set(context.evidence).issubset(artifact.evidence_ids):
            issues.append(
                _artifact_issue(
                    context.task_id,
                    "ARTIFACT_CITATION_COVERAGE_INCOMPLETE",
                    "Report artifact does not cite all workflow evidence",
                )
            )
        try:
            raw = report_mapping_from_bytes(content, artifact.type)
        except (UnicodeDecodeError, ValueError):
            issues.append(
                _artifact_issue(
                    context.task_id,
                    "ARTIFACT_REPORT_MODEL_INVALID",
                    "Report Artifact does not carry a readable structured model",
                )
            )
            return tuple(issues), empty
        try:
            report_step_id = next(
                step.step_id
                for step in context.plan.steps
                if step.step_type is StepType.REPORT_GENERATION
            )
            candidate = candidate_from_json_report(
                task_contract=context.contract,
                report_step_id=report_step_id,
                report=raw,
                evidence=tuple(context.evidence.values()),
            )
        except (StopIteration, TypeError, ValueError):
            issues.append(
                _artifact_issue(
                    context.task_id,
                    "ARTIFACT_REPORT_MODEL_INVALID",
                    "JSON report could not be mapped to structured verification claims",
                )
            )
            return tuple(issues), empty
        return tuple(issues), candidate


def _artifact_issue(task_id: str, code: str, message: str) -> VerificationIssue:
    return VerificationIssue(
        code=code,
        message=message,
        severity=VerificationSeverity.ERROR,
        verifier="ArtifactIntegrityVerifier",
        task_id=task_id,
        details=JsonObject({}),
    )


__all__ = ["WorkflowVerifier"]
