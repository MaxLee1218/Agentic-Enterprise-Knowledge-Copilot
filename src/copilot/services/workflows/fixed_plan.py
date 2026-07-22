"""Frozen deterministic plan factory for Supplier Quality Analysis v1.0."""

from __future__ import annotations

from copilot.contracts import (
    CapabilityName,
    RetryPolicy,
    StepType,
    TaskContract,
    TaskPlan,
    TaskRequest,
    TaskStep,
)
from copilot.tools.registry import ToolRegistry

SUPPLIER_QUALITY_PLAN_ID = "supplier-quality-analysis-v1"
SUPPLIER_QUALITY_PLAN_VERSION = 1

RETRIEVE_POLICY = "retrieve-quality-policy"
QUERY_QUALITY_DATA = "query-supplier-quality-data"
ANALYZE_QUALITY = "analyze-supplier-quality"
GENERATE_REPORT = "generate-supplier-quality-report"


def step_id(task_id: str, template_id: str) -> str:
    """Bind a stable template suffix to one globally unique Task."""
    return f"{task_id}:{template_id}"


class SupplierQualityAnalysisPlanFactory:
    """Create the immutable four-step v1 plan from registered frozen definitions."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def create(self, request: TaskRequest, contract: TaskContract) -> TaskPlan:
        """Create a deterministic task-bound plan without model planning."""
        del request
        task_id = contract.task_id
        kb = self._step(
            task_id=task_id,
            template_id=RETRIEVE_POLICY,
            step_type=StepType.KNOWLEDGE_SEARCH,
            tool_name=CapabilityName.KNOWLEDGE_SEARCH,
            dependencies=(),
            retry_policy=RetryPolicy(
                max_attempts=3,
                backoff_seconds=(1, 2),
                retryable_error_codes=("KNOWLEDGE_UNAVAILABLE", "KNOWLEDGE_TIMEOUT"),
            ),
        )
        database = self._step(
            task_id=task_id,
            template_id=QUERY_QUALITY_DATA,
            step_type=StepType.DATABASE_QUERY,
            tool_name=CapabilityName.DATABASE_QUERY,
            dependencies=(),
            retry_policy=RetryPolicy(
                max_attempts=3,
                backoff_seconds=(1, 2),
                retryable_error_codes=("DATABASE_UNAVAILABLE", "DATABASE_TIMEOUT"),
            ),
        )
        analytics = self._step(
            task_id=task_id,
            template_id=ANALYZE_QUALITY,
            step_type=StepType.ANALYSIS,
            tool_name=CapabilityName.ANALYSIS_ENGINE,
            dependencies=(database.step_id,),
            retry_policy=RetryPolicy(
                max_attempts=2,
                backoff_seconds=(1,),
                retryable_error_codes=("ANALYSIS_ENGINE_FAILURE", "ANALYSIS_TIMEOUT"),
            ),
        )
        report = self._step(
            task_id=task_id,
            template_id=GENERATE_REPORT,
            step_type=StepType.REPORT_GENERATION,
            tool_name=CapabilityName.REPORT_GENERATOR,
            dependencies=(kb.step_id, analytics.step_id),
            retry_policy=RetryPolicy(
                max_attempts=2,
                backoff_seconds=(1,),
                retryable_error_codes=("REPORT_GENERATION_FAILURE", "REPORT_TIMEOUT"),
            ),
        )
        return TaskPlan(
            task_id=task_id,
            steps=(kb, database, analytics, report),
            planning_version=SUPPLIER_QUALITY_PLAN_VERSION,
        )

    def _step(
        self,
        *,
        task_id: str,
        template_id: str,
        step_type: StepType,
        tool_name: CapabilityName,
        dependencies: tuple[str, ...],
        retry_policy: RetryPolicy,
    ) -> TaskStep:
        definition = self._registry.get(tool_name.value).definition
        return TaskStep(
            step_id=step_id(task_id, template_id),
            task_id=task_id,
            step_type=step_type,
            tool_name=definition.tool_name,
            input_schema=definition.input_schema,
            output_schema=definition.output_schema,
            dependency=dependencies,
            retry_policy=retry_policy,
        )
