"""Typed test object builders for domain-contract unit tests."""

from datetime import UTC, date, datetime

from copilot.contracts import (
    ApprovalRequirement,
    ArtifactType,
    CapabilityName,
    ExpectedOutput,
    JsonObject,
    ReportLanguage,
    RetryPolicy,
    StepType,
    TaskConstraints,
    TaskContract,
    TaskPlan,
    TaskStep,
    TaskType,
)

TASK_ID = "T-001"
STARTED_AT = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 7, 19, 8, 0, 1, 250000, tzinfo=UTC)


def make_constraints() -> TaskConstraints:
    """Build a valid frozen supplier-quality scope."""
    return TaskConstraints(
        year=2026,
        quarter=1,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 3, 31),
        supplier_ids=("S-100", "S-200"),
        tenant_id="TENANT-A",
        data_scope=("quality.v1",),
        metrics=("defect_count", "inspected_count", "defect_rate"),
        deadline_at=datetime(2026, 7, 19, 9, 0, tzinfo=UTC),
    )


def make_contract() -> TaskContract:
    """Build a valid versioned task contract."""
    return TaskContract(
        task_id=TASK_ID,
        contract_version=1,
        task_type=TaskType.SUPPLIER_QUALITY_ANALYSIS_V1,
        required_capabilities=tuple(CapabilityName),
        expected_output=ExpectedOutput(
            artifact_type=ArtifactType.QUALITY_ANALYSIS_REPORT_PDF,
            required_sections=("scope", "metrics", "findings", "limitations", "evidence"),
            language=ReportLanguage.ZH_CN,
            citations_required=True,
        ),
        constraints=make_constraints(),
        approval_requirement=ApprovalRequirement(
            required=True,
            policy_id="quality-confidential-v1",
            approver_role="quality_data_approver",
            controlled_scope=("S-100", "S-200", "2026-Q1"),
        ),
    )


def make_step(
    step_id: str,
    step_type: StepType,
    tool_name: str,
    dependency: tuple[str, ...] = (),
) -> TaskStep:
    """Build one valid task step with strict schemas and bounded retry."""
    schema = JsonObject({"type": "object", "additionalProperties": False})
    return TaskStep(
        step_id=step_id,
        task_id=TASK_ID,
        step_type=step_type,
        tool_name=tool_name,
        input_schema=schema,
        output_schema=schema,
        dependency=dependency,
        retry_policy=RetryPolicy(
            max_attempts=2,
            backoff_seconds=(1,),
            retryable_error_codes=("DEPENDENCY_UNAVAILABLE",),
        ),
    )


def make_plan() -> TaskPlan:
    """Build a valid two-node DAG."""
    database = make_step("S-DB-01", StepType.DATABASE_QUERY, "database_query")
    analysis = make_step(
        "S-AN-01",
        StepType.ANALYSIS,
        "analysis_engine",
        dependency=(database.step_id,),
    )
    return TaskPlan(task_id=TASK_ID, steps=(database, analysis), planning_version=1)
