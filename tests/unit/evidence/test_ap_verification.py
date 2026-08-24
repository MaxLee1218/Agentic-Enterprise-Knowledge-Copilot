"""Frozen Stage 6 Accounts Payable claim and verifier-profile coverage."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from pydantic import JsonValue

from copilot.contracts import (
    ACCOUNTS_PAYABLE_CONTRACT_PROFILES,
    AccountsPayableConstraintsV1,
    APExceptionType,
    ApprovalRequirement,
    ArtifactType,
    CandidateResult,
    CapabilityName,
    CitationClaim,
    ClaimType,
    ContractSchemaVersion,
    DateRange,
    DeliverableRecord,
    EvidenceItem,
    EvidenceType,
    ExpectedOutput,
    JsonObject,
    MoneyThreshold,
    NumericClaim,
    ReportLanguage,
    RetryPolicy,
    StepResult,
    StepResultStatus,
    StepType,
    TaskContract,
    TaskPlan,
    TaskStep,
    TaskType,
    VerificationContext,
    VerificationStatus,
)
from copilot.contracts.base import JsonMapping
from copilot.evidence.ap_validators import APNumericVerifier
from copilot.evidence.citations import candidate_from_ap_report
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.evidence.profiles import (
    ACCOUNTS_PAYABLE_VERIFIER_PROFILE,
    ACCOUNTS_PAYABLE_VERIFIER_PROFILE_ID,
    SUPPLIER_QUALITY_VERIFIER_PROFILE_ID,
    composite_verifier_for_profile,
)
from copilot.security import SensitiveDataRegistry
from copilot.services.domains.manifests import ACCOUNTS_PAYABLE_MANIFEST
from copilot.tools.analytics import AccountsPayableAnalyticsTool
from copilot.tools.analytics.ap_schemas import APAnalyticsOperation, APDatabaseTemplate
from tests.unit.evidence.helpers import valid_candidate, valid_contract, valid_ledger, valid_plan
from tests.unit.evidence.helpers import valid_step_results as valid_sq_step_results
from tests.unit.evidence.helpers import valid_verification_context as valid_sq_context
from tests.unit.tools.analytics.ap_helpers import (
    FIXED_TIME,
    add_database_dataset,
    add_policy_evidence,
    aggregation_arguments,
    analytics_context,
    detection_arguments,
    duplicate_row,
    evidence_ledger,
    payment_amount_row,
    payment_terms_row,
    po_row,
    population_row,
)

TASK_ID = "T-AP-AN-001"
TENANT_ID = "TENANT-DEMO"


@dataclass(frozen=True, slots=True)
class APVerificationFixture:
    contract: TaskContract
    plan: TaskPlan
    step_results: dict[str, StepResult]
    ledger: InMemoryEvidenceLedger
    context: VerificationContext
    candidate: CandidateResult
    summary_ids: tuple[str, ...]


def _build_fixture() -> APVerificationFixture:
    ledger = evidence_ledger()
    rule_snapshot, document_ids = add_policy_evidence(ledger)
    manifest = cast(JsonMapping, rule_snapshot["rule_manifest"])
    population = [
        population_row("I-001", payment_count=1, settled_payment_count=1),
        population_row(
            "I-002",
            payment_count=1,
            settled_payment_count=1,
            po_record_key=None,
            po_matching_basis=None,
            po_status=None,
        ),
    ]
    datasets = {
        APDatabaseTemplate.INVOICE_POPULATION: add_database_dataset(
            ledger, APDatabaseTemplate.INVOICE_POPULATION, population
        ),
        APDatabaseTemplate.DUPLICATE_CANDIDATES: add_database_dataset(
            ledger,
            APDatabaseTemplate.DUPLICATE_CANDIDATES,
            [duplicate_row(row) for row in population],
        ),
        APDatabaseTemplate.INVOICE_PO_VARIANCE: add_database_dataset(
            ledger,
            APDatabaseTemplate.INVOICE_PO_VARIANCE,
            [
                po_row(population[0], approved_amount="800.0000"),
                po_row(population[1], po_record_key=None),
            ],
        ),
        APDatabaseTemplate.PAYMENT_TERMS: add_database_dataset(
            ledger,
            APDatabaseTemplate.PAYMENT_TERMS,
            [payment_terms_row(row, payment_date="2026-06-05") for row in population],
        ),
        APDatabaseTemplate.PAYMENT_AMOUNT: add_database_dataset(
            ledger,
            APDatabaseTemplate.PAYMENT_AMOUNT,
            [payment_amount_row(row, payment_amount="1200.0000") for row in population],
        ),
    }
    operation_templates = {
        APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION: (
            APDatabaseTemplate.DUPLICATE_CANDIDATES
        ),
        APAnalyticsOperation.INVOICE_PO_VARIANCE_DETECTION: (
            APDatabaseTemplate.INVOICE_PO_VARIANCE
        ),
        APAnalyticsOperation.MISSING_PO_DETECTION: APDatabaseTemplate.INVOICE_PO_VARIANCE,
        APAnalyticsOperation.PAYMENT_TERM_COMPLIANCE_DETECTION: APDatabaseTemplate.PAYMENT_TERMS,
        APAnalyticsOperation.OVERPAYMENT_DETECTION: APDatabaseTemplate.PAYMENT_AMOUNT,
    }
    tool = AccountsPayableAnalyticsTool(ledger)
    calculation_ids: list[str] = []
    for index, (operation, template) in enumerate(operation_templates.items(), start=1):
        arguments = detection_arguments(
            operation,
            datasets[APDatabaseTemplate.INVOICE_POPULATION],
            datasets[template],
            rule_snapshot,
        )
        context = analytics_context(arguments, call_id=f"TC-AP-DET-{index}")
        execution = tool.execute(arguments, context)
        calculation_ids.extend(
            item.evidence_id for item in ledger.record(context.call, execution.evidence)
        )

    summary_ids: tuple[str, ...] = ()
    for operation in (
        APAnalyticsOperation.EXCEPTION_SUMMARY,
        APAnalyticsOperation.SUPPLIER_EXCEPTION_RATE,
    ):
        arguments = aggregation_arguments(
            operation,
            datasets[APDatabaseTemplate.INVOICE_POPULATION],
            rule_snapshot,
            tuple(calculation_ids),
        )
        call_name = "SUMMARY" if operation is APAnalyticsOperation.EXCEPTION_SUMMARY else "SUPPLIER"
        context = analytics_context(arguments, call_id=f"TC-AP-{call_name}")
        execution = tool.execute(arguments, context)
        stored = ledger.record(context.call, execution.evidence)
        if operation is APAnalyticsOperation.EXCEPTION_SUMMARY:
            summary_ids = tuple(item.evidence_id for item in stored)

    contract = _contract(cast(str, manifest["manifest_checksum"]))
    plan, step_results = _plan_and_results(ledger)
    summary = ledger.get(summary_ids[0], task_id=TASK_ID, tenant_id=TENANT_ID)
    raw_rule_ids = summary.source_reference.reference.root["rule_ids"]
    assert isinstance(raw_rule_ids, list)
    rule_ids = tuple(item for item in raw_rule_ids if isinstance(item, str))
    policy_id = next(iter(document_ids.values()))
    candidate = _candidate(contract, summary_ids, policy_id, rule_ids)
    verification_context = VerificationContext(
        trace_id="TRACE-AP-STAGE6",
        verifier_profile_id=ACCOUNTS_PAYABLE_VERIFIER_PROFILE_ID,
        registered_tools=(),
        tool_results=(),
        allowed_tables=ACCOUNTS_PAYABLE_VERIFIER_PROFILE.allowed_tables,
        allowed_columns=ACCOUNTS_PAYABLE_VERIFIER_PROFILE.allowed_columns,
        allowed_query_templates=ACCOUNTS_PAYABLE_VERIFIER_PROFILE.allowed_query_templates,
        sensitive_fields=ACCOUNTS_PAYABLE_VERIFIER_PROFILE.sensitive_fields,
    )
    return APVerificationFixture(
        contract=contract,
        plan=plan,
        step_results=step_results,
        ledger=ledger,
        context=verification_context,
        candidate=candidate,
        summary_ids=summary_ids,
    )


def _contract(manifest_checksum: str) -> TaskContract:
    snapshot_at = datetime(2026, 10, 1, tzinfo=UTC)
    return TaskContract(
        contract_schema_version=ContractSchemaVersion.TASK_CONTRACT_V2,
        task_id=TASK_ID,
        contract_version=1,
        task_type=TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1,
        goal="Verify controlled Accounts Payable exception analysis",
        required_capabilities=tuple(CapabilityName),
        expected_output=ExpectedOutput(
            artifact_type=ArtifactType.ACCOUNTS_PAYABLE_REPORT_JSON,
            required_sections=(
                "scope",
                "data_overview",
                "applicable_policies",
                "exception_summary",
                "duplicate_invoice_findings",
                "po_compliance_findings",
                "payment_findings",
                "supplier_summary",
                "risk_observations",
                "recommended_actions",
                "limitations",
                "evidence",
                "execution_trace",
            ),
            language=ReportLanguage.EN_US,
            citations_required=True,
        ),
        constraints=AccountsPayableConstraintsV1(
            time_range=DateRange(start_date=date(2026, 4, 1), end_date=date(2026, 6, 30)),
            legal_entity_ids=("LE-US-01",),
            exception_types=tuple(APExceptionType),
            effective_materiality=(
                MoneyThreshold(currency="CNY", amount=Decimal("5000.0000")),
                MoneyThreshold(currency="USD", amount=Decimal("1000.0000")),
            ),
            tenant_id=TENANT_ID,
            data_scope=("accounts_payable.v1", "accounts-payable-policy-v1"),
            policy_rule_set_id="accounts-payable-v1",
            policy_rule_set_version="ap_rules.2026.1",
            policy_manifest_checksum=manifest_checksum,
            snapshot_at=snapshot_at,
            deadline_at=snapshot_at + timedelta(minutes=5),
        ),
        approval_requirement=ApprovalRequirement(required=False),
        created_at=FIXED_TIME,
    )


def _plan_and_results(
    ledger: InMemoryEvidenceLedger,
) -> tuple[TaskPlan, dict[str, StepResult]]:
    evidence_by_step: dict[str, list[str]] = defaultdict(list)
    source_by_step: dict[str, EvidenceType] = {}
    for item in ledger.list(TASK_ID, tenant_id=TENANT_ID):
        evidence_by_step[item.step_id].append(item.evidence_id)
        source_by_step[item.step_id] = item.source_type
    specs = {
        EvidenceType.DOCUMENT: (StepType.KNOWLEDGE_SEARCH, CapabilityName.KNOWLEDGE_SEARCH),
        EvidenceType.DATABASE: (StepType.DATABASE_QUERY, CapabilityName.DATABASE_QUERY),
        EvidenceType.CALCULATION: (StepType.ANALYSIS, CapabilityName.ANALYSIS_ENGINE),
    }
    steps: list[TaskStep] = []
    results: dict[str, StepResult] = {}
    for step_id in sorted(evidence_by_step):
        step_type, capability = specs[source_by_step[step_id]]
        steps.append(
            TaskStep(
                step_id=step_id,
                task_id=TASK_ID,
                step_type=step_type,
                tool_name=capability.value,
                tool_version="stage6-fixture.v1",
                contract_profile=ACCOUNTS_PAYABLE_CONTRACT_PROFILES[capability],
                input_schema=JsonObject({}),
                output_schema=JsonObject({}),
                retry_policy=RetryPolicy(max_attempts=1),
            )
        )
        results[step_id] = StepResult(
            step_id=step_id,
            status=StepResultStatus.SUCCESS,
            output=JsonObject({"verified_fixture": True}),
            evidence=tuple(evidence_by_step[step_id]),
            error=None,
        )
    steps.append(
        TaskStep(
            step_id="S-AP-REPORT",
            task_id=TASK_ID,
            step_type=StepType.REPORT_GENERATION,
            tool_name=CapabilityName.REPORT_GENERATOR.value,
            tool_version="stage7-not-implemented",
            contract_profile=ACCOUNTS_PAYABLE_CONTRACT_PROFILES[CapabilityName.REPORT_GENERATOR],
            input_schema=JsonObject({}),
            output_schema=JsonObject({}),
            retry_policy=RetryPolicy(max_attempts=1),
        )
    )
    results["S-AP-REPORT"] = StepResult(
        step_id="S-AP-REPORT",
        status=StepResultStatus.SUCCESS,
        output=JsonObject({"structured_candidate_only": True}),
        evidence=(),
        error=None,
    )
    return (
        TaskPlan(task_id=TASK_ID, steps=tuple(steps), planning_version=1, created_at=FIXED_TIME),
        results,
    )


def _candidate(
    contract: TaskContract,
    summary_ids: tuple[str, ...],
    policy_id: str,
    rule_ids: tuple[str, ...],
) -> CandidateResult:
    refs = tuple(dict.fromkeys((*summary_ids, policy_id)))
    return CandidateResult(
        task_id=TASK_ID,
        deliverables=tuple(
            DeliverableRecord(
                deliverable_id=section,
                producing_step_id="S-AP-REPORT",
                content={"present": True},
                evidence_ids=refs,
            )
            for section in contract.expected_output.required_sections
        ),
        claims=(
            CitationClaim(
                claim_id="ap:policy",
                claim_type=ClaimType.POLICY,
                evidence_ids=(policy_id,),
                step_id="S-AP-REPORT",
            ),
            CitationClaim(
                claim_id="ap:invoice-count",
                claim_type=ClaimType.NUMERIC,
                evidence_ids=summary_ids,
                step_id="S-AP-REPORT",
                policy_governed=True,
                rule_ids=rule_ids,
            ),
        ),
        numeric_claims=(
            NumericClaim(
                claim_id="ap:invoice-count",
                metric_name="invoice_count",
                value=2,
                unit="count",
                precision=0,
                evidence_ids=summary_ids,
                operation_name=APAnalyticsOperation.EXCEPTION_SUMMARY.value,
            ),
        ),
        output_fields=("invoice_count", "applicable_policies"),
    )


@pytest.fixture(scope="module")
def ap_fixture() -> APVerificationFixture:
    return _build_fixture()


def _arguments(fixture: APVerificationFixture) -> dict[str, object]:
    return {
        "task_contract": fixture.contract,
        "task_plan": fixture.plan,
        "step_results": fixture.step_results,
        "evidence_ledger": fixture.ledger,
        "verification_context": fixture.context,
        "candidate_result": fixture.candidate,
    }


def test_ap_profile_passes_and_uc1_default_order_is_unchanged(
    ap_fixture: APVerificationFixture,
) -> None:
    assert ACCOUNTS_PAYABLE_MANIFEST.verifier_profile == ACCOUNTS_PAYABLE_VERIFIER_PROFILE_ID
    assert ACCOUNTS_PAYABLE_MANIFEST.execution_enabled is True
    result = composite_verifier_for_profile(ACCOUNTS_PAYABLE_VERIFIER_PROFILE_ID).verify(
        **_arguments(ap_fixture)  # type: ignore[arg-type]
    )
    assert result.status is VerificationStatus.PASSED
    assert [check.verifier for check in result.checks] == [
        "EvidenceStructureVerifier",
        "APEvidenceMetadataVerifier",
        "DeliverableVerifier",
        "CitationVerifier",
        "APPolicyBindingVerifier",
        "APConsistencyVerifier",
        "APNumericVerifier",
        "SafetyVerifier",
    ]

    supplier = composite_verifier_for_profile(SUPPLIER_QUALITY_VERIFIER_PROFILE_ID).verify(
        task_contract=valid_contract(),
        task_plan=valid_plan(),
        step_results=valid_sq_step_results(),
        evidence_ledger=valid_ledger(),
        verification_context=valid_sq_context(),
        candidate_result=valid_candidate(),
    )
    assert [check.verifier for check in supplier.checks] == [
        "EvidenceStructureVerifier",
        "DeliverableVerifier",
        "CitationVerifier",
        "NumericVerifier",
        "SafetyVerifier",
    ]


def test_ap_report_adapter_maps_explicit_claims_without_narrative_parsing(
    ap_fixture: APVerificationFixture,
) -> None:
    policy_claim, numeric_claim = ap_fixture.candidate.claims
    report: dict[str, JsonValue] = {
        section: {"present": True}
        for section in ap_fixture.contract.expected_output.required_sections
    }
    report["data_overview"] = {"empty_result": False}
    report["evidence"] = {
        "claims": [
            {
                "claim_id": policy_claim.claim_id,
                "claim_type": "POLICY",
                "evidence_ids": list(policy_claim.evidence_ids),
            },
            {
                "claim_id": numeric_claim.claim_id,
                "claim_type": "NUMERIC",
                "evidence_ids": list(numeric_claim.evidence_ids),
                "policy_governed": True,
                "rule_ids": list(numeric_claim.rule_ids),
                "metric_name": "invoice_count",
                "value": 2,
                "unit": "count",
                "precision": 0,
                "operation_name": APAnalyticsOperation.EXCEPTION_SUMMARY.value,
                "dimensions": {},
            },
        ]
    }
    candidate = candidate_from_ap_report(
        task_contract=ap_fixture.contract,
        report_step_id="S-AP-REPORT",
        report=report,
        evidence=ap_fixture.ledger.list(TASK_ID, tenant_id=TENANT_ID),
    )
    assert candidate.numeric_claims[0].value == 2
    assert candidate.claims[1].policy_governed is True
    assert len(candidate.deliverables) == len(ap_fixture.contract.expected_output.required_sections)

    percent_claim = cast(dict[str, JsonValue], report["evidence"])["claims"]
    assert isinstance(percent_claim, list)
    numeric_payload = cast(dict[str, JsonValue], percent_claim[1])
    numeric_payload["metric_name"] = "exception_rate"
    numeric_payload["value"] = "50.00"
    numeric_payload["unit"] = "percent"
    numeric_payload["precision"] = 2
    percent_candidate = candidate_from_ap_report(
        task_contract=ap_fixture.contract,
        report_step_id="S-AP-REPORT",
        report=report,
        evidence=ap_fixture.ledger.list(TASK_ID, tenant_id=TENANT_ID),
    )
    assert percent_candidate.numeric_claims[0].value == Decimal("0.50")
    canonical_ratio = percent_candidate.numeric_claims[0].value
    assert isinstance(canonical_ratio, Decimal)
    assert canonical_ratio.as_tuple().exponent == -8
    assert percent_candidate.numeric_claims[0].unit == "ratio"
    assert percent_candidate.numeric_claims[0].precision == 8


def test_ap_report_adapter_marks_empty_population_sections_as_valid_empty_results(
    ap_fixture: APVerificationFixture,
) -> None:
    report: dict[str, JsonValue] = {
        section: {"present": True}
        for section in ap_fixture.contract.expected_output.required_sections
    }
    for section in (
        "duplicate_invoice_findings",
        "po_compliance_findings",
        "payment_findings",
        "supplier_summary",
    ):
        report[section] = []
    report["data_overview"] = {"empty_result": True}
    report["evidence"] = {"claims": []}

    candidate = candidate_from_ap_report(
        task_contract=ap_fixture.contract,
        report_step_id="S-AP-REPORT",
        report=report,
        evidence=ap_fixture.ledger.list(TASK_ID, tenant_id=TENANT_ID),
    )

    by_id = {item.deliverable_id: item for item in candidate.deliverables}
    assert all(
        by_id[section].empty_result
        for section in (
            "duplicate_invoice_findings",
            "po_compliance_findings",
            "payment_findings",
            "supplier_summary",
        )
    )


def test_ap_report_adapter_accepts_individually_empty_finding_sections(
    ap_fixture: APVerificationFixture,
) -> None:
    report: dict[str, JsonValue] = {
        section: {"present": True}
        for section in ap_fixture.contract.expected_output.required_sections
    }
    report["duplicate_invoice_findings"] = []
    report["payment_findings"] = []
    report["data_overview"] = {"empty_result": False}
    report["evidence"] = {"claims": []}

    candidate = candidate_from_ap_report(
        task_contract=ap_fixture.contract,
        report_step_id="S-AP-REPORT",
        report=report,
        evidence=ap_fixture.ledger.list(TASK_ID, tenant_id=TENANT_ID),
    )

    by_id = {item.deliverable_id: item for item in candidate.deliverables}
    assert by_id["duplicate_invoice_findings"].empty_result
    assert by_id["payment_findings"].empty_result
    assert not by_id["po_compliance_findings"].empty_result
    assert not by_id["data_overview"].empty_result
    assert not by_id["exception_summary"].empty_result


@pytest.mark.parametrize(
    ("candidate_update", "expected_code"),
    [
        ({"value": 3}, "AP_NUMERIC_CLAIM_MISMATCH"),
        ({"unit": "money", "precision": 4}, "AP_NUMERIC_UNIT_PRECISION_MISMATCH"),
        ({"evidence_ids": ("E-UNKNOWN",)}, "AP_NUMERIC_BASELINE_NOT_UNIQUE"),
    ],
)
def test_ap_numeric_tampering_fails(
    ap_fixture: APVerificationFixture,
    candidate_update: dict[str, object],
    expected_code: str,
) -> None:
    numeric = ap_fixture.candidate.numeric_claims[0].model_copy(update=candidate_update)
    candidate = ap_fixture.candidate.model_copy(update={"numeric_claims": (numeric,)})
    arguments = _arguments(ap_fixture)
    arguments["candidate_result"] = candidate
    issues = APNumericVerifier().verify(**arguments)  # type: ignore[arg-type]
    assert expected_code in {issue.code for issue in issues}


def test_ap_money_currency_and_duplicate_baseline_fail_closed(
    ap_fixture: APVerificationFixture,
) -> None:
    rule_ids = ap_fixture.candidate.claims[1].rule_ids
    money_claim = NumericClaim(
        claim_id="ap:invoice-amount-usd",
        metric_name="invoice_amount_by_currency",
        value=Decimal("2000.0000"),
        unit="money",
        precision=4,
        evidence_ids=ap_fixture.summary_ids,
        dimensions=JsonObject({"currency": "USD"}),
        operation_name=APAnalyticsOperation.EXCEPTION_SUMMARY.value,
    )
    arguments = _arguments(ap_fixture)
    arguments["candidate_result"] = ap_fixture.candidate.model_copy(
        update={"numeric_claims": (money_claim,)}
    )
    assert APNumericVerifier().verify(**arguments) == ()  # type: ignore[arg-type]
    unscaled = money_claim.model_copy(update={"value": Decimal("2000")})
    arguments["candidate_result"] = ap_fixture.candidate.model_copy(
        update={"numeric_claims": (unscaled,)}
    )
    issues = APNumericVerifier().verify(**arguments)  # type: ignore[arg-type]
    assert "AP_NUMERIC_CLAIM_MISMATCH" in {issue.code for issue in issues}

    cny_only = ap_fixture.contract.model_copy(
        update={
            "constraints": ap_fixture.contract.constraints.model_copy(
                update={"currency_scope": ("CNY",)}
            )
        }
    )
    arguments = _arguments(ap_fixture)
    arguments["task_contract"] = cny_only
    arguments["candidate_result"] = ap_fixture.candidate.model_copy(
        update={"numeric_claims": (money_claim,)}
    )
    issues = APNumericVerifier().verify(**arguments)  # type: ignore[arg-type]
    assert "AP_NUMERIC_CURRENCY_MISMATCH" in {issue.code for issue in issues}

    summary = ap_fixture.ledger.get(ap_fixture.summary_ids[0], task_id=TASK_ID, tenant_id=TENANT_ID)
    policy = next(
        item
        for item in ap_fixture.ledger.list(TASK_ID, tenant_id=TENANT_ID)
        if item.source_type is EvidenceType.DOCUMENT
    )
    extra_policy_reference = dict(policy.source_reference.reference.root)
    extra_policy_reference["document_id"] = "AP-POLICY-DUPLICATE-BASELINE-TEST"
    extra_policy = policy.model_copy(
        update={
            "evidence_id": "E-AP-DOC-DUPLICATE-BASELINE",
            "source_reference": policy.source_reference.model_copy(
                update={"reference": JsonObject(extra_policy_reference)}
            ),
        }
    )
    duplicate_reference = dict(summary.source_reference.reference.root)
    duplicate_reference["calculation_run_id"] = "APCALC-DUPLICATE-BASELINE"
    duplicate = summary.model_copy(
        update={
            "evidence_id": "E-AP-CALC-DUPLICATE",
            "source_reference": summary.source_reference.model_copy(
                update={
                    "reference": JsonObject(duplicate_reference),
                    "input_evidence_ids": (
                        *summary.source_reference.input_evidence_ids,
                        extra_policy.evidence_id,
                    ),
                }
            ),
        }
    )
    duplicate_ledger = _copy_ledger_with_addition(ap_fixture.ledger, extra_policy)
    duplicate_ledger.add(duplicate, tenant_id=TENANT_ID)
    count_claim = ap_fixture.candidate.numeric_claims[0].model_copy(
        update={"evidence_ids": (*ap_fixture.summary_ids, duplicate.evidence_id)}
    )
    arguments = _arguments(ap_fixture)
    arguments["evidence_ledger"] = duplicate_ledger
    arguments["candidate_result"] = ap_fixture.candidate.model_copy(
        update={"numeric_claims": (count_claim,)}
    )
    issues = APNumericVerifier().verify(**arguments)  # type: ignore[arg-type]
    assert "AP_NUMERIC_BASELINE_NOT_UNIQUE" in {issue.code for issue in issues}
    assert rule_ids


def test_rule_scope_batch_truncation_and_restricted_output_tampering_fail(
    ap_fixture: APVerificationFixture,
) -> None:
    composite = composite_verifier_for_profile(ACCOUNTS_PAYABLE_VERIFIER_PROFILE_ID)

    wrong_policy = ap_fixture.contract.model_copy(
        update={
            "constraints": ap_fixture.contract.constraints.model_copy(
                update={"policy_manifest_checksum": "sha256:wrong-policy-manifest"}
            )
        }
    )
    arguments = _arguments(ap_fixture)
    arguments["task_contract"] = wrong_policy
    result = composite.verify(**arguments)  # type: ignore[arg-type]
    assert result.status is VerificationStatus.FAILED
    assert "AP_POLICY_MANIFEST_MISMATCH" in {issue.code for issue in result.issues}

    wrong_scope = ap_fixture.contract.model_copy(
        update={
            "constraints": ap_fixture.contract.constraints.model_copy(
                update={"supplier_ids": ("SUP-OUTSIDE",)}
            )
        }
    )
    arguments["task_contract"] = wrong_scope
    result = composite.verify(**arguments)  # type: ignore[arg-type]
    assert "AP_RECORD_SCOPE_MISMATCH" in {issue.code for issue in result.issues}

    database = next(
        item
        for item in ap_fixture.ledger.list(TASK_ID, tenant_id=TENANT_ID)
        if item.source_type is EvidenceType.DATABASE
    )
    truncated_data = dict(database.content.data.root)
    truncated_data["truncated"] = True
    truncated = database.model_copy(
        update={"content": database.content.model_copy(update={"data": JsonObject(truncated_data)})}
    )
    truncated_ledger = _rebuild_ledger(ap_fixture.ledger, replacement=truncated)
    arguments = _arguments(ap_fixture)
    arguments["evidence_ledger"] = truncated_ledger
    result = composite.verify(**arguments)  # type: ignore[arg-type]
    assert "AP_DATABASE_TRUNCATED" in {issue.code for issue in result.issues}

    calculation = ap_fixture.ledger.get(
        ap_fixture.summary_ids[0], task_id=TASK_ID, tenant_id=TENANT_ID
    )
    reference = dict(calculation.source_reference.reference.root)
    reference["batch_count"] = 2
    incomplete = calculation.model_copy(
        update={
            "source_reference": calculation.source_reference.model_copy(
                update={"reference": JsonObject(reference)}
            )
        }
    )
    incomplete_ledger = _rebuild_ledger(ap_fixture.ledger, replacement=incomplete)
    arguments["evidence_ledger"] = incomplete_ledger
    result = composite.verify(**arguments)  # type: ignore[arg-type]
    assert "AP_CALCULATION_BATCH_INCOMPLETE" in {issue.code for issue in result.issues}

    restricted_candidate = ap_fixture.candidate.model_copy(
        update={"output_fields": ("payment_reference",)}
    )
    arguments = _arguments(ap_fixture)
    arguments["candidate_result"] = restricted_candidate
    result = composite.verify(**arguments)  # type: ignore[arg-type]
    assert result.status is VerificationStatus.FAILED
    assert "SENSITIVE_FIELD_OUTPUT" in {issue.code for issue in result.issues}


def _rebuild_ledger(
    source: InMemoryEvidenceLedger, *, replacement: EvidenceItem
) -> InMemoryEvidenceLedger:
    ledger = InMemoryEvidenceLedger(max_items_per_task=500)
    for item in source.list(TASK_ID, tenant_id=TENANT_ID):
        ledger.add(
            replacement if item.evidence_id == replacement.evidence_id else item,
            tenant_id=TENANT_ID,
        )
    return ledger


def _copy_ledger_with_addition(
    source: InMemoryEvidenceLedger, addition: EvidenceItem
) -> InMemoryEvidenceLedger:
    ledger = _rebuild_ledger(source, replacement=addition)
    if not ledger.validate_reference(TASK_ID, addition.evidence_id, tenant_id=TENANT_ID):
        ledger.add(addition, tenant_id=TENANT_ID)
    return ledger


def test_sensitive_registry_contains_all_frozen_ap_aliases() -> None:
    registry = SensitiveDataRegistry()
    for field in ("swift", "tax_id", "payment_reference", "internal_account_number"):
        assert registry.policy_for(field) is not None
