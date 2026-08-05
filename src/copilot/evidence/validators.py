"""Independent deterministic evidence, deliverable, citation, numeric, and safety verifiers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from time import perf_counter
from typing import Protocol, cast

from pydantic import JsonValue

from copilot.contracts import (
    ApprovalStatus,
    CandidateResult,
    ClaimType,
    DeliverableRecord,
    EvidenceItem,
    EvidenceType,
    JsonObject,
    LineageTrace,
    StepResult,
    StepResultStatus,
    TaskContract,
    TaskPlan,
    VerificationCheck,
    VerificationContext,
    VerificationIssue,
    VerificationResult,
    VerificationSeverity,
    VerificationStatus,
)
from copilot.contracts.base import JsonMapping
from copilot.contracts.validators import utc_now
from copilot.evidence.lineage import contains_source_type
from copilot.policies.approval import action_fingerprint, schema_fingerprint
from copilot.security import (
    ContentSourceType,
    OutputDisposition,
    OutputGuard,
    SensitiveDataRegistry,
)
from copilot.tools.analytics.precision import DECIMAL_PLACES


class EvidenceLedgerView(Protocol):
    """Task-scoped read operations required by deterministic verifiers."""

    def list(self, task_id: str) -> tuple[EvidenceItem, ...]:
        """List evidence owned by a task."""
        ...

    def get(self, evidence_id: str, *, task_id: str | None = None) -> EvidenceItem:
        """Resolve one evidence item."""
        ...

    def validate_reference(self, task_id: str, evidence_id: str) -> bool:
        """Validate task ownership of an identifier."""
        ...

    def trace_lineage(self, task_id: str, evidence_id: str) -> LineageTrace:
        """Trace all parents of an evidence item."""
        ...


class DeterministicVerifier(Protocol):
    """Common verifier interface composed by ``CompositeVerifier``."""

    name: str

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
        """Return all ordinary verification problems without raising."""
        ...


class EvidenceStructureVerifier:
    """Validate evidence ownership, source metadata, references, and lineage."""

    name = "EvidenceStructureVerifier"

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
        del verification_context, candidate_result
        task_id = task_contract.task_id
        issues: list[VerificationIssue] = []
        planned_steps = {step.step_id for step in task_plan.steps}
        evidence = evidence_ledger.list(task_id)
        for item in evidence:
            if item.step_id not in planned_steps:
                issues.append(
                    _issue(
                        code="EVIDENCE_STEP_NOT_PLANNED",
                        message="Evidence was produced by a step outside the validated plan",
                        verifier=self.name,
                        task_id=task_id,
                        step_id=item.step_id,
                        evidence_ids=(item.evidence_id,),
                    )
                )
            issues.extend(_source_metadata_issues(item, self.name))
            if item.source_type is EvidenceType.CALCULATION:
                trace = evidence_ledger.trace_lineage(task_id, item.evidence_id)
                issues.extend(_lineage_issues(trace, self.name))
                if not contains_source_type(trace, EvidenceType.DATABASE):
                    issues.append(
                        _issue(
                            code="CALCULATION_DATABASE_LINEAGE_MISSING",
                            message="Calculation evidence cannot be traced to database evidence",
                            verifier=self.name,
                            task_id=task_id,
                            step_id=item.step_id,
                            evidence_ids=(item.evidence_id,),
                        )
                    )
        for step_id, result in step_results.items():
            for evidence_id in result.evidence:
                if not evidence_ledger.validate_reference(task_id, evidence_id):
                    issues.append(
                        _issue(
                            code="STEP_EVIDENCE_REFERENCE_INVALID",
                            message="Step result references missing or cross-task evidence",
                            verifier=self.name,
                            task_id=task_id,
                            step_id=step_id,
                            evidence_ids=(evidence_id,),
                        )
                    )
        return tuple(issues)


class DeliverableVerifier:
    """Check exact frozen deliverable identifiers and their producing step."""

    name = "DeliverableVerifier"

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
        del verification_context
        task_id = task_contract.task_id
        issues: list[VerificationIssue] = []
        records: dict[str, DeliverableRecord] = {}
        for deliverable in candidate_result.deliverables:
            if deliverable.deliverable_id in records:
                issues.append(
                    _issue(
                        code="DELIVERABLE_DUPLICATE",
                        message="Candidate contains a duplicate deliverable identifier",
                        verifier=self.name,
                        task_id=task_id,
                        step_id=deliverable.producing_step_id,
                    )
                )
            records[deliverable.deliverable_id] = deliverable

        for deliverable_id in task_contract.expected_output.required_sections:
            raw = records.get(deliverable_id)
            if raw is None:
                issues.append(
                    _issue(
                        code="DELIVERABLE_MISSING",
                        message="A required deliverable is missing",
                        verifier=self.name,
                        task_id=task_id,
                        details={"deliverable_id": deliverable_id},
                    )
                )
                continue
            record = raw
            step = next(
                (
                    planned
                    for planned in task_plan.steps
                    if planned.step_id == record.producing_step_id
                ),
                None,
            )
            result = step_results.get(record.producing_step_id)
            if step is None or result is None or result.status is not StepResultStatus.SUCCESS:
                issues.append(
                    _issue(
                        code="DELIVERABLE_STEP_NOT_SUCCESSFUL",
                        message="The step responsible for a required deliverable did not succeed",
                        verifier=self.name,
                        task_id=task_id,
                        step_id=record.producing_step_id,
                    )
                )
            if _is_empty_content(record.content):
                if not record.empty_result:
                    issues.append(
                        _issue(
                            code="DELIVERABLE_CONTENT_EMPTY",
                            message="A required deliverable has no meaningful structured content",
                            verifier=self.name,
                            task_id=task_id,
                            step_id=record.producing_step_id,
                            details={"deliverable_id": deliverable_id},
                        )
                    )
                elif deliverable_id == "analysis_results":
                    issues.append(
                        _issue(
                            code="EMPTY_RESULT_EXPLANATION_MISSING",
                            message="An empty analysis must retain explicit structured coverage",
                            verifier=self.name,
                            task_id=task_id,
                            step_id=record.producing_step_id,
                        )
                    )
            for evidence_id in record.evidence_ids:
                if not evidence_ledger.validate_reference(task_id, evidence_id):
                    issues.append(
                        _issue(
                            code="DELIVERABLE_EVIDENCE_INVALID",
                            message="Deliverable references missing or cross-task evidence",
                            verifier=self.name,
                            task_id=task_id,
                            step_id=record.producing_step_id,
                            evidence_ids=(evidence_id,),
                        )
                    )
        return tuple(issues)


class CitationVerifier:
    """Validate structured claim citations and evidence-type compatibility."""

    name = "CitationVerifier"

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
        del task_plan, step_results, verification_context
        task_id = task_contract.task_id
        issues: list[VerificationIssue] = []
        for claim in candidate_result.claims:
            if not claim.evidence_ids:
                issues.append(
                    _issue(
                        code="CITATION_REQUIRED",
                        message="A structured claim has no evidence references",
                        verifier=self.name,
                        task_id=task_id,
                        step_id=claim.step_id,
                        claim_id=claim.claim_id,
                    )
                )
                continue
            traces: list[LineageTrace] = []
            for evidence_id in claim.evidence_ids:
                if not evidence_ledger.validate_reference(task_id, evidence_id):
                    issues.append(
                        _issue(
                            code="CITATION_REFERENCE_INVALID",
                            message="Claim cites missing or cross-task evidence",
                            verifier=self.name,
                            task_id=task_id,
                            step_id=claim.step_id,
                            claim_id=claim.claim_id,
                            evidence_ids=(evidence_id,),
                        )
                    )
                    continue
                item = evidence_ledger.get(evidence_id, task_id=task_id)
                issues.extend(_source_metadata_issues(item, self.name, claim.claim_id))
                trace = evidence_ledger.trace_lineage(task_id, evidence_id)
                traces.append(trace)
                issues.extend(_lineage_issues(trace, self.name, claim.claim_id))
            required_type = {
                ClaimType.POLICY: EvidenceType.DOCUMENT,
                ClaimType.DATA: EvidenceType.DATABASE,
                ClaimType.NUMERIC: EvidenceType.CALCULATION,
            }.get(claim.claim_type)
            if required_type is not None and not any(
                contains_source_type(trace, required_type) for trace in traces
            ):
                issues.append(
                    _issue(
                        code="CITATION_TYPE_INCOMPATIBLE",
                        message="Claim evidence does not contain the required source type",
                        verifier=self.name,
                        task_id=task_id,
                        step_id=claim.step_id,
                        claim_id=claim.claim_id,
                        evidence_ids=claim.evidence_ids,
                        details={"required_source_type": required_type.value},
                    )
                )
            if claim.claim_type in {ClaimType.DATA, ClaimType.NUMERIC} and not any(
                contains_source_type(trace, EvidenceType.DATABASE) for trace in traces
            ):
                issues.append(
                    _issue(
                        code="DATA_CLAIM_DATABASE_LINEAGE_MISSING",
                        message="Data claim cannot be traced to database evidence",
                        verifier=self.name,
                        task_id=task_id,
                        step_id=claim.step_id,
                        claim_id=claim.claim_id,
                        evidence_ids=claim.evidence_ids,
                    )
                )
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class NumericTolerancePolicy:
    """Central explicit precision policy for deterministic numeric comparison."""

    decimal_places: int = DECIMAL_PLACES
    rounding: str = ROUND_HALF_EVEN

    def tolerance(self, precision: int) -> Decimal:
        """Return half of the least significant reported decimal place."""
        return Decimal(1).scaleb(-precision) / Decimal(2)


class NumericVerifier:
    """Compare structured numeric claims with Calculation Evidence using Decimal."""

    name = "NumericVerifier"

    def __init__(self, policy: NumericTolerancePolicy | None = None) -> None:
        self._policy = policy or NumericTolerancePolicy()

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
        del task_plan, step_results, verification_context
        task_id = task_contract.task_id
        issues: list[VerificationIssue] = []
        for claim in candidate_result.numeric_claims:
            if not claim.evidence_ids:
                issues.append(
                    _numeric_issue(
                        "NUMERIC_EVIDENCE_MISSING",
                        "Numeric claim has no calculation evidence",
                        task_id,
                        claim.claim_id,
                    )
                )
                continue
            baselines = _metric_baselines(
                task_id,
                claim.metric_name,
                claim.dimensions.root,
                claim.evidence_ids,
                evidence_ledger,
            )
            if len(baselines) != 1:
                issues.append(
                    _numeric_issue(
                        "NUMERIC_BASELINE_NOT_UNIQUE",
                        "Numeric claim does not resolve to exactly one analytics baseline",
                        task_id,
                        claim.claim_id,
                        evidence_ids=claim.evidence_ids,
                    )
                )
                continue
            baseline, evidence_id = baselines[0]
            expected_unit = baseline.get("unit")
            if claim.unit != expected_unit:
                issues.append(
                    _numeric_issue(
                        "NUMERIC_UNIT_MISMATCH",
                        "Numeric claim unit differs from analytics evidence",
                        task_id,
                        claim.claim_id,
                        evidence_ids=(evidence_id,),
                        details={"expected_unit": str(expected_unit), "actual_unit": claim.unit},
                    )
                )
                continue
            expected_value = _decimal_value(baseline.get("value"))
            actual_value = _decimal_value(claim.value)
            if expected_value is None or actual_value is None:
                if expected_value != actual_value:
                    issues.append(
                        _numeric_issue(
                            "NUMERIC_VALUE_MISMATCH",
                            "Numeric null/value semantics differ from analytics evidence",
                            task_id,
                            claim.claim_id,
                            evidence_ids=(evidence_id,),
                        )
                    )
                continue
            if not expected_value.is_finite() or not actual_value.is_finite():
                issues.append(
                    _numeric_issue(
                        "NUMERIC_VALUE_NON_FINITE",
                        "Numeric values must not be NaN or infinite",
                        task_id,
                        claim.claim_id,
                        evidence_ids=(evidence_id,),
                    )
                )
                continue
            if claim.unit == "count":
                matches = (
                    expected_value == expected_value.to_integral_value()
                    and actual_value == actual_value.to_integral_value()
                    and expected_value == actual_value
                )
            else:
                if claim.precision != self._policy.decimal_places:
                    issues.append(
                        _numeric_issue(
                            "NUMERIC_PRECISION_MISMATCH",
                            "Numeric claim precision differs from analytics precision policy",
                            task_id,
                            claim.claim_id,
                            evidence_ids=(evidence_id,),
                            details={
                                "expected_precision": self._policy.decimal_places,
                                "actual_precision": claim.precision,
                            },
                        )
                    )
                quantum = Decimal(1).scaleb(-claim.precision)
                rounded_expected = expected_value.quantize(quantum, rounding=self._policy.rounding)
                matches = abs(actual_value - rounded_expected) <= self._policy.tolerance(
                    claim.precision
                )
            if not matches:
                issues.append(
                    _numeric_issue(
                        "NUMERIC_CLAIM_MISMATCH",
                        "Numeric claim differs from deterministic analytics evidence",
                        task_id,
                        claim.claim_id,
                        evidence_ids=(evidence_id,),
                        details={"metric_name": claim.metric_name, "unit": claim.unit},
                    )
                )
            expected_ranking = baseline.get("ranking")
            if claim.ranking and (
                not isinstance(expected_ranking, list)
                or tuple(str(value) for value in expected_ranking) != claim.ranking
            ):
                issues.append(
                    _numeric_issue(
                        "NUMERIC_RANKING_MISMATCH",
                        "Claim ranking order differs from analytics evidence",
                        task_id,
                        claim.claim_id,
                        evidence_ids=(evidence_id,),
                    )
                )
        return tuple(issues)


class SafetyVerifier:
    """Validate registered execution, approval, database, and sensitive-field metadata."""

    name = "SafetyVerifier"

    def __init__(self, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock

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
        task_id = task_contract.task_id
        issues: list[VerificationIssue] = []
        definitions = {
            definition.tool_name: definition for definition in verification_context.registered_tools
        }
        planned = {step.step_id: step for step in task_plan.steps}
        calls = {call.tool_call_id: call for call in verification_context.tool_calls}
        allowed_capabilities = {
            capability.value for capability in task_contract.required_capabilities
        }
        approvals = {approval.approval_id: approval for approval in verification_context.approvals}
        now = self._clock()
        requirement = task_contract.approval_requirement
        if requirement.required and not any(
            approval.task_id == task_id
            and approval.tenant_id == task_contract.constraints.tenant_id
            and approval.planning_version == task_plan.planning_version
            and approval.status is ApprovalStatus.APPROVED
            and approval.expires_at > now
            and set(requirement.controlled_scope).issubset(approval.controlled_scope)
            for approval in approvals.values()
        ):
            issues.append(
                _safety_issue(
                    "APPROVAL_REQUIRED",
                    "The task has no valid approval covering its controlled scope",
                    task_id,
                    None,
                )
            )
        for result in verification_context.tool_results:
            definition = definitions.get(result.tool_name)
            step = planned.get(result.step_id)
            call = calls.get(result.tool_call_id)
            if definition is None:
                issues.append(
                    _safety_issue(
                        "TOOL_NOT_REGISTERED",
                        "An executed tool is not present in the registry snapshot",
                        task_id,
                        result.step_id,
                    )
                )
            if step is None or step.tool_name != result.tool_name:
                issues.append(
                    _safety_issue(
                        "TOOL_NOT_IN_PLAN",
                        "An executed tool does not match the validated plan",
                        task_id,
                        result.step_id,
                    )
                )
            if result.tool_name not in allowed_capabilities:
                issues.append(
                    _safety_issue(
                        "TOOL_CAPABILITY_NOT_ALLOWED",
                        "An executed tool is outside the task capability contract",
                        task_id,
                        result.step_id,
                    )
                )
            if call is None or (
                call.task_id != task_id
                or call.step_id != result.step_id
                or call.tool_name != result.tool_name
            ):
                issues.append(
                    _safety_issue(
                        "TOOL_EXECUTOR_LINEAGE_MISSING",
                        "Tool result lacks a matching governed call envelope",
                        task_id,
                        result.step_id,
                    )
                )
            if result.task_id != task_id:
                issues.append(
                    _safety_issue(
                        "TOOL_RESULT_CROSS_TASK",
                        "Tool result belongs to another task",
                        task_id,
                        result.step_id,
                    )
                )
            if result.status.value == "PERMISSION_DENIED":
                issues.append(
                    _safety_issue(
                        "TOOL_PERMISSION_DENIED",
                        "Execution contains a permission-denied tool attempt",
                        task_id,
                        result.step_id,
                    )
                )
            if verification_context.readonly_task and result.tool_name not in {
                "knowledge_search",
                "database_query",
                "analysis_engine",
                "report_generator",
            }:
                issues.append(
                    _safety_issue(
                        "READONLY_TASK_WRITE_TOOL",
                        "Read-only task executed a non-read-only capability",
                        task_id,
                        result.step_id,
                    )
                )
            if call is not None and call.approval_id is not None:
                approval = approvals.get(call.approval_id or "")
                if approval is None:
                    issues.append(
                        _safety_issue(
                            "APPROVAL_REQUIRED",
                            "A controlled invocation has no matching approval record",
                            task_id,
                            result.step_id,
                        )
                    )
                else:
                    definition = definitions.get(call.tool_name)
                    expected_fingerprint = (
                        action_fingerprint(
                            task_id=approval.task_id,
                            planning_version=approval.planning_version,
                            step_id=approval.step_id,
                            tool_name=approval.tool_name,
                            tool_version=approval.tool_version,
                            input_schema_fingerprint=approval.input_schema_fingerprint,
                            controlled_scope=approval.controlled_scope,
                            arguments=call.input,
                        )
                        if definition is not None
                        else ""
                    )
                    invalid = (
                        definition is None
                        or approval.task_id != task_id
                        or approval.tenant_id != call.tenant_id
                        or approval.step_id != call.step_id
                        or approval.tool_name != call.tool_name
                        or approval.tool_version != call.tool_version
                        or approval.planning_version != task_plan.planning_version
                        or approval.status is not ApprovalStatus.APPROVED
                        or approval.expires_at <= now
                        or approval.resolved_arguments != call.input
                        or approval.input_schema_fingerprint != schema_fingerprint(definition)
                        or approval.resolved_action_fingerprint != expected_fingerprint
                        or not set(requirement.controlled_scope).issubset(approval.controlled_scope)
                    )
                    if invalid:
                        issues.append(
                            _safety_issue(
                                "APPROVAL_SCOPE_INVALID",
                                (
                                    "Approval does not match the task, plan, action, scope, "
                                    "or validity"
                                ),
                                task_id,
                                result.step_id,
                            )
                        )

        allowed_tables = set(verification_context.allowed_tables)
        allowed_columns = set(verification_context.allowed_columns)
        sensitive_fields = set(verification_context.sensitive_fields) | set(
            SensitiveDataRegistry().sensitive_names()
        )
        sensitive_names = sensitive_fields | {
            field.rsplit(".", 1)[-1] for field in sensitive_fields
        }
        output_guard = OutputGuard()
        for item in evidence_ledger.list(task_id):
            reference = item.source_reference.reference.root
            if reference.get("quarantined") is True:
                issues.append(
                    _safety_issue(
                        "QUARANTINED_EVIDENCE",
                        "Quarantined untrusted content cannot support the final result",
                        task_id,
                        item.step_id,
                        (item.evidence_id,),
                    )
                )
            guarded_evidence = output_guard.guard(
                item.content.data.root,
                source_type=ContentSourceType.TOOL_OUTPUT,
                source_id=item.evidence_id,
                target="verification",
            )
            if guarded_evidence.disposition is OutputDisposition.BLOCKED:
                issues.append(
                    _safety_issue(
                        "UNSAFE_EVIDENCE_CONTENT",
                        "Evidence contains content blocked by the safety policy",
                        task_id,
                        item.step_id,
                        (item.evidence_id,),
                    )
                )
            if item.source_type is not EvidenceType.DATABASE:
                continue
            query_id = reference.get("query_id") or reference.get("query_fingerprint")
            if not isinstance(query_id, str) or not query_id.strip():
                issues.append(
                    _safety_issue(
                        "DATABASE_QUERY_ID_MISSING",
                        "Database evidence has no stable query fingerprint",
                        task_id,
                        item.step_id,
                        (item.evidence_id,),
                    )
                )
            if (
                reference.get("statement_type") != "SELECT"
                or reference.get("read_only") is not True
            ):
                issues.append(
                    _safety_issue(
                        "DATABASE_READONLY_METADATA_INVALID",
                        "Database evidence does not prove a read-only SELECT operation",
                        task_id,
                        item.step_id,
                        (item.evidence_id,),
                    )
                )
            table_names = _string_sequence(reference.get("table_names"))
            if not table_names:
                issues.append(
                    _safety_issue(
                        "DATABASE_TABLE_METADATA_MISSING",
                        "Database evidence has no structured table audit metadata",
                        task_id,
                        item.step_id,
                        (item.evidence_id,),
                    )
                )
            for table in table_names:
                if table not in allowed_tables:
                    issues.append(
                        _safety_issue(
                            "DATABASE_TABLE_NOT_ALLOWED",
                            "Database evidence references an unregistered table",
                            task_id,
                            item.step_id,
                            (item.evidence_id,),
                            {"table_name": table},
                        )
                    )
            column_names = _string_sequence(reference.get("column_names"))
            if not column_names:
                issues.append(
                    _safety_issue(
                        "DATABASE_COLUMN_METADATA_MISSING",
                        "Database evidence has no structured field audit metadata",
                        task_id,
                        item.step_id,
                        (item.evidence_id,),
                    )
                )
            for column in column_names:
                if column not in allowed_columns:
                    issues.append(
                        _safety_issue(
                            "DATABASE_COLUMN_NOT_ALLOWED",
                            "Database evidence references an unregistered field",
                            task_id,
                            item.step_id,
                            (item.evidence_id,),
                            {"column_name": column},
                        )
                    )
                if column in sensitive_fields:
                    issues.append(
                        _safety_issue(
                            "DATABASE_SENSITIVE_FIELD",
                            "Database evidence includes a restricted sensitive field",
                            task_id,
                            item.step_id,
                            (item.evidence_id,),
                            {"column_name": column},
                        )
                    )
        leaked = sorted(
            field
            for field in candidate_result.output_fields
            if field in sensitive_fields or field.rsplit(".", 1)[-1] in sensitive_names
        )
        for field in leaked:
            issues.append(
                _safety_issue(
                    "SENSITIVE_FIELD_OUTPUT",
                    "Candidate result exposes a configured sensitive field",
                    task_id,
                    evidence_ids=(),
                    details={"field_name": field},
                )
            )
        return tuple(issues)


class CompositeVerifier:
    """Run every safe verifier and aggregate one deterministic result."""

    def __init__(
        self,
        verifiers: Sequence[DeterministicVerifier] | None = None,
        *,
        clock: Callable[[], datetime] = utc_now,
        timer: Callable[[], float] = perf_counter,
    ) -> None:
        self._verifiers = tuple(
            verifiers
            or (
                EvidenceStructureVerifier(),
                DeliverableVerifier(),
                CitationVerifier(),
                NumericVerifier(),
                SafetyVerifier(clock=clock),
            )
        )
        self._clock = clock
        self._timer = timer

    def verify(
        self,
        *,
        task_contract: TaskContract,
        task_plan: TaskPlan,
        step_results: Mapping[str, StepResult],
        evidence_ledger: EvidenceLedgerView,
        verification_context: VerificationContext,
        candidate_result: CandidateResult,
    ) -> VerificationResult:
        """Run all verifiers even when an earlier one reports ordinary errors."""
        started = self._timer()
        all_issues: list[VerificationIssue] = []
        checks: list[VerificationCheck] = []
        evidence_ids = tuple(
            item.evidence_id for item in evidence_ledger.list(task_contract.task_id)
        )
        for verifier in self._verifiers:
            issues = verifier.verify(
                task_contract=task_contract,
                task_plan=task_plan,
                step_results=step_results,
                evidence_ledger=evidence_ledger,
                verification_context=verification_context,
                candidate_result=candidate_result,
            )
            all_issues.extend(issues)
            checks.append(
                VerificationCheck(
                    verifier=verifier.name,
                    passed=not any(
                        issue.severity is VerificationSeverity.ERROR for issue in issues
                    ),
                    issue_codes=tuple(issue.code for issue in issues),
                    verified_evidence_ids=evidence_ids,
                )
            )
        warning_count = sum(issue.severity is VerificationSeverity.WARNING for issue in all_issues)
        error_count = sum(issue.severity is VerificationSeverity.ERROR for issue in all_issues)
        status = (
            VerificationStatus.FAILED
            if error_count
            else (
                VerificationStatus.PASSED_WITH_WARNINGS
                if warning_count
                else VerificationStatus.PASSED
            )
        )
        duration_ms = max(0, round((self._timer() - started) * 1000))
        return VerificationResult(
            task_id=task_contract.task_id,
            trace_id=verification_context.trace_id,
            status=status,
            issues=tuple(all_issues),
            checks=tuple(checks),
            verified_at=self._clock(),
            duration_ms=duration_ms,
            warning_count=warning_count,
            error_count=error_count,
            verified_evidence_ids=evidence_ids,
        )


def _source_metadata_issues(
    item: EvidenceItem,
    verifier: str,
    claim_id: str | None = None,
) -> tuple[VerificationIssue, ...]:
    reference = item.source_reference.reference.root
    valid = True
    code = ""
    message = ""
    if item.source_type is EvidenceType.DOCUMENT:
        valid = bool(
            (reference.get("document_id") or reference.get("source"))
            and (reference.get("chunk_id") or reference.get("page") is not None)
        )
        code = "DOCUMENT_SOURCE_INVALID"
        message = "Document evidence lacks a stable document and location reference"
    elif item.source_type is EvidenceType.DATABASE:
        valid = bool(reference.get("query_id") or reference.get("query_fingerprint"))
        code = "DATABASE_QUERY_ID_MISSING"
        message = "Database evidence lacks a stable query fingerprint"
    elif item.source_type is EvidenceType.CALCULATION:
        valid = bool(item.source_reference.input_evidence_ids)
        code = "CALCULATION_LINEAGE_MISSING"
        message = "Calculation evidence has no input evidence"
    if valid:
        return ()
    return (
        _issue(
            code=code,
            message=message,
            verifier=verifier,
            task_id=item.task_id,
            step_id=item.step_id,
            claim_id=claim_id,
            evidence_ids=(item.evidence_id,),
        ),
    )


def _lineage_issues(
    trace: LineageTrace,
    verifier: str,
    claim_id: str | None = None,
) -> tuple[VerificationIssue, ...]:
    return tuple(
        _issue(
            code=issue.code,
            message=issue.message,
            verifier=verifier,
            task_id=trace.task_id,
            claim_id=claim_id,
            evidence_ids=tuple(
                value
                for value in (issue.evidence_id, issue.parent_evidence_id)
                if value is not None
            ),
        )
        for issue in trace.issues
    )


def _metric_baselines(
    task_id: str,
    metric_name: str,
    dimensions: JsonMapping,
    evidence_ids: tuple[str, ...],
    ledger: EvidenceLedgerView,
) -> list[tuple[JsonMapping, str]]:
    matches: list[tuple[JsonMapping, str]] = []
    for evidence_id in evidence_ids:
        if not ledger.validate_reference(task_id, evidence_id):
            continue
        item = ledger.get(evidence_id, task_id=task_id)
        if item.source_type is not EvidenceType.CALCULATION:
            continue
        raw_metrics = item.content.data.root.get("metrics")
        if not isinstance(raw_metrics, list):
            continue
        for metric in raw_metrics:
            if not isinstance(metric, dict) or metric.get("metric") != metric_name:
                continue
            raw_dimensions = metric.get("dimensions")
            if dimensions and raw_dimensions != dimensions:
                continue
            matches.append((metric, evidence_id))
    return matches


def _decimal_value(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Decimal | int | float | str):
        return Decimal("NaN")
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal("NaN")


def _is_empty_content(value: JsonValue) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _string_sequence(value: JsonValue | None) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str))


def _issue(
    *,
    code: str,
    message: str,
    verifier: str,
    task_id: str,
    step_id: str | None = None,
    claim_id: str | None = None,
    evidence_ids: tuple[str, ...] = (),
    details: Mapping[str, JsonValue] | None = None,
    severity: VerificationSeverity = VerificationSeverity.ERROR,
) -> VerificationIssue:
    return VerificationIssue(
        code=code,
        message=message,
        severity=severity,
        verifier=verifier,
        task_id=task_id,
        step_id=step_id,
        claim_id=claim_id,
        evidence_ids=evidence_ids,
        details=JsonObject(cast(JsonMapping, dict(details or {}))),
    )


def _numeric_issue(
    code: str,
    message: str,
    task_id: str,
    claim_id: str,
    *,
    evidence_ids: tuple[str, ...] = (),
    details: Mapping[str, JsonValue] | None = None,
) -> VerificationIssue:
    return _issue(
        code=code,
        message=message,
        verifier=NumericVerifier.name,
        task_id=task_id,
        claim_id=claim_id,
        evidence_ids=evidence_ids,
        details=details,
    )


def _safety_issue(
    code: str,
    message: str,
    task_id: str,
    step_id: str | None = None,
    evidence_ids: tuple[str, ...] = (),
    details: Mapping[str, JsonValue] | None = None,
) -> VerificationIssue:
    return _issue(
        code=code,
        message=message,
        verifier=SafetyVerifier.name,
        task_id=task_id,
        step_id=step_id,
        evidence_ids=evidence_ids,
        details=details,
    )


__all__ = [
    "CitationVerifier",
    "CompositeVerifier",
    "DeliverableVerifier",
    "DeterministicVerifier",
    "EvidenceLedgerView",
    "EvidenceStructureVerifier",
    "NumericTolerancePolicy",
    "NumericVerifier",
    "SafetyVerifier",
]
