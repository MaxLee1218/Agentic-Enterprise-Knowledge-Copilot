"""Typed builders for evidence and verifier unit tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast

from copilot.contracts import (
    ApprovalRequirement,
    ArtifactType,
    CandidateResult,
    CapabilityName,
    CitationClaim,
    ClaimType,
    DeliverableRecord,
    EvidenceContent,
    EvidenceItem,
    EvidenceSourceReference,
    EvidenceType,
    ExpectedOutput,
    JsonObject,
    NumericClaim,
    ReportLanguage,
    RetryPolicy,
    StepResult,
    StepResultStatus,
    StepType,
    TaskConstraints,
    TaskContract,
    TaskPlan,
    TaskStep,
    TaskType,
    ToolCall,
    ToolDefinition,
    ToolResult,
    ToolResultStatus,
    VerificationContext,
)
from copilot.contracts.base import JsonMapping
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.tools.mock_supplier_quality import (
    MockAnalyticsTool,
    MockDatabaseTool,
    MockKnowledgeTool,
    MockReportTool,
)

NOW = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
TASK_ID = "T-VERIFY-001"
TENANT_ID = "TENANT-A"
DOC_ID = "E-DOC-001"
DB_ID = "E-DB-001"
CALC_ID = "E-CALC-001"


def evidence_item(
    evidence_id: str,
    source_type: EvidenceType,
    *,
    task_id: str = TASK_ID,
    step_id: str | None = None,
    parents: tuple[str, ...] = (),
    reference: dict[str, object] | None = None,
    data: dict[str, object] | None = None,
) -> EvidenceItem:
    """Build one valid immutable EvidenceItem."""
    references = {
        EvidenceType.DOCUMENT: {
            "document_id": "DOC-QUALITY",
            "document_version": "v1",
            "chunk_id": "chunk-1",
        },
        EvidenceType.DATABASE: {
            "query_template_id": "supplier_quality_summary_v1",
            "query_fingerprint": "sha256:query",
            "table_names": ["incoming_inspections", "suppliers"],
            "column_names": [
                "incoming_inspections.rejected_quantity",
                "incoming_inspections.total_quantity",
                "suppliers.supplier_code",
            ],
            "statement_type": "SELECT",
            "read_only": True,
        },
        EvidenceType.CALCULATION: {
            "formula": "defect_count / inspected_count",
            "engine_version": "quality_metrics.v1",
            "group_by": ["scope"],
        },
    }
    contents = {
        EvidenceType.DOCUMENT: {"excerpt": "Defect rate uses inspected count."},
        EvidenceType.DATABASE: {
            "row_count": 2,
            "inspected_count": 1000,
            "defect_count": 15,
        },
        EvidenceType.CALCULATION: {
            "metrics": [
                {
                    "metric": "defect_rate",
                    "dimensions": {"scope": "all_suppliers"},
                    "value": 0.015,
                    "unit": "ratio",
                    "numerator": 15,
                    "denominator": 1000,
                },
                {
                    "metric": "defect_count",
                    "dimensions": {"scope": "all_suppliers"},
                    "value": 15,
                    "unit": "count",
                    "numerator": 15,
                    "denominator": None,
                },
            ],
            "warnings": [],
        },
    }
    return EvidenceItem(
        evidence_id=evidence_id,
        task_id=task_id,
        step_id=step_id or f"S-{source_type.value}",
        tool_call_id=f"TC-{evidence_id}",
        source_type=source_type,
        source_reference=EvidenceSourceReference(
            reference=JsonObject(cast(JsonMapping, reference or references[source_type])),
            input_evidence_ids=parents,
        ),
        content=EvidenceContent(
            data=JsonObject(cast(JsonMapping, data or contents[source_type])),
            classification="CONFIDENTIAL",
            checksum=f"sha256:{evidence_id}",
        ),
        timestamp=NOW,
    )


def valid_ledger() -> InMemoryEvidenceLedger:
    """Build Document, Database, and Calculation evidence with valid lineage."""
    ledger = InMemoryEvidenceLedger()
    ledger.add(evidence_item(DOC_ID, EvidenceType.DOCUMENT, step_id="S-KB"), tenant_id=TENANT_ID)
    ledger.add(evidence_item(DB_ID, EvidenceType.DATABASE, step_id="S-DB"), tenant_id=TENANT_ID)
    ledger.add(
        evidence_item(
            CALC_ID,
            EvidenceType.CALCULATION,
            step_id="S-AN",
            parents=(DB_ID,),
        ),
        tenant_id=TENANT_ID,
    )
    return ledger


def tool_definitions() -> tuple[ToolDefinition, ...]:
    """Return the four current frozen mock tool definitions."""
    return (
        MockKnowledgeTool.definition,
        MockDatabaseTool.definition,
        MockAnalyticsTool.definition,
        MockReportTool.definition,
    )


def valid_contract() -> TaskContract:
    """Build a minimal contract with deterministic section identifiers."""
    return TaskContract(
        task_id=TASK_ID,
        contract_version=1,
        task_type=TaskType.SUPPLIER_QUALITY_ANALYSIS_V1,
        required_capabilities=tuple(CapabilityName),
        expected_output=ExpectedOutput(
            artifact_type=ArtifactType.QUALITY_ANALYSIS_REPORT_JSON,
            required_sections=("policy", "data", "analysis"),
            language=ReportLanguage.EN_US,
            citations_required=True,
        ),
        constraints=TaskConstraints(
            year=2026,
            quarter=1,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 31),
            supplier_ids=("SUP-001",),
            tenant_id="TENANT-A",
            data_scope=("quality.v1",),
            metrics=("defect_rate", "defect_count"),
            deadline_at=NOW + timedelta(minutes=5),
        ),
        approval_requirement=ApprovalRequirement(required=False),
        created_at=NOW,
    )


def valid_plan() -> TaskPlan:
    """Build a valid four-step task plan."""
    definitions = {item.tool_name: item for item in tool_definitions()}

    def make(
        step_id: str,
        step_type: StepType,
        tool_name: str,
        dependencies: tuple[str, ...] = (),
    ) -> TaskStep:
        definition = definitions[tool_name]
        return TaskStep(
            step_id=step_id,
            task_id=TASK_ID,
            step_type=step_type,
            tool_name=tool_name,
            input_schema=definition.input_schema,
            output_schema=definition.output_schema,
            dependency=dependencies,
            retry_policy=RetryPolicy(max_attempts=1),
        )

    kb = make("S-KB", StepType.KNOWLEDGE_SEARCH, "knowledge_search")
    database = make("S-DB", StepType.DATABASE_QUERY, "database_query")
    analytics = make("S-AN", StepType.ANALYSIS, "analysis_engine", ("S-DB",))
    report = make(
        "S-RP",
        StepType.REPORT_GENERATION,
        "report_generator",
        ("S-KB", "S-AN"),
    )
    return TaskPlan(
        task_id=TASK_ID,
        steps=(kb, database, analytics, report),
        planning_version=1,
        created_at=NOW,
    )


def valid_step_results() -> dict[str, StepResult]:
    """Build successful results with the evidence produced by each step."""
    return {
        "S-KB": StepResult(
            step_id="S-KB",
            status=StepResultStatus.SUCCESS,
            output=JsonObject({"matches": [{}]}),
            evidence=(DOC_ID,),
            error=None,
        ),
        "S-DB": StepResult(
            step_id="S-DB",
            status=StepResultStatus.SUCCESS,
            output=JsonObject({"row_count": 2, "empty_result": False}),
            evidence=(DB_ID,),
            error=None,
        ),
        "S-AN": StepResult(
            step_id="S-AN",
            status=StepResultStatus.SUCCESS,
            output=JsonObject({"metrics": [{}], "empty_result": False}),
            evidence=(CALC_ID,),
            error=None,
        ),
        "S-RP": StepResult(
            step_id="S-RP",
            status=StepResultStatus.SUCCESS,
            output=JsonObject({"artifact_id": "A-001"}),
            evidence=(),
            error=None,
        ),
    }


def valid_candidate() -> CandidateResult:
    """Build exact deliverables, compatible citations, and two numeric claims."""
    refs = (DOC_ID, DB_ID, CALC_ID)
    return CandidateResult(
        task_id=TASK_ID,
        deliverables=tuple(
            DeliverableRecord(
                deliverable_id=name,
                producing_step_id="S-RP",
                content={"present": True},
                evidence_ids=refs,
            )
            for name in ("policy", "data", "analysis")
        ),
        claims=(
            CitationClaim(
                claim_id="C-POLICY",
                claim_type=ClaimType.POLICY,
                evidence_ids=(DOC_ID,),
            ),
            CitationClaim(
                claim_id="C-DATA",
                claim_type=ClaimType.DATA,
                evidence_ids=(DB_ID,),
            ),
            CitationClaim(
                claim_id="C-RATE",
                claim_type=ClaimType.NUMERIC,
                evidence_ids=(CALC_ID,),
            ),
        ),
        numeric_claims=(
            NumericClaim(
                claim_id="C-RATE",
                metric_name="defect_rate",
                value=Decimal("0.0150"),
                unit="ratio",
                precision=4,
                evidence_ids=(CALC_ID,),
                dimensions=JsonObject({"scope": "all_suppliers"}),
            ),
            NumericClaim(
                claim_id="C-COUNT",
                metric_name="defect_count",
                value=15,
                unit="count",
                precision=0,
                evidence_ids=(CALC_ID,),
                dimensions=JsonObject({"scope": "all_suppliers"}),
            ),
        ),
        output_fields=("policy", "data", "analysis"),
    )


def valid_verification_context(plan: TaskPlan | None = None) -> VerificationContext:
    """Build matching ToolCall and ToolResult envelopes for SafetyVerifier."""
    selected_plan = plan or valid_plan()
    definitions = {item.tool_name: item for item in tool_definitions()}
    calls: list[ToolCall] = []
    results: list[ToolResult] = []
    for index, step in enumerate(selected_plan.steps, start=1):
        definition = definitions[step.tool_name]
        call_id = f"TC-{index}"
        call = ToolCall(
            tool_call_id=call_id,
            task_id=TASK_ID,
            step_id=step.step_id,
            tool_name=step.tool_name,
            tool_version=definition.tool_version,
            input=JsonObject({}),
            idempotency_key=f"action-{index}",
            approval_id=None,
            deadline_at=NOW + timedelta(minutes=1),
            tenant_id="TENANT-A",
            user_id="U-001",
        )
        calls.append(call)
        results.append(
            ToolResult(
                tool_call_id=call_id,
                task_id=TASK_ID,
                step_id=step.step_id,
                tool_name=step.tool_name,
                tool_version=definition.tool_version,
                status=ToolResultStatus.SUCCESS,
                output=JsonObject({}),
                error=None,
                started_at=NOW,
                completed_at=NOW,
                attempt=1,
            )
        )
    return VerificationContext(
        trace_id="TRACE-001",
        registered_tools=tool_definitions(),
        tool_calls=tuple(calls),
        tool_results=tuple(results),
        allowed_tables=("incoming_inspections", "suppliers"),
        allowed_columns=(
            "incoming_inspections.rejected_quantity",
            "incoming_inspections.total_quantity",
            "suppliers.supplier_code",
        ),
        sensitive_fields=("secret_value",),
    )
