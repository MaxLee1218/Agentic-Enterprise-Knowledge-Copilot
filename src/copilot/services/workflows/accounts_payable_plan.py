"""Deterministic fixed plan for the frozen Accounts Payable analysis profile."""

from __future__ import annotations

from dataclasses import dataclass

from copilot.contracts import (
    ACCOUNTS_PAYABLE_CONTRACT_PROFILES,
    AccountsPayableConstraintsV1,
    APAnalyticsOperation,
    APDatabaseTemplate,
    APExceptionType,
    CapabilityName,
    RetryPolicy,
    StepType,
    TaskContract,
    TaskPlan,
    TaskRequest,
    TaskStep,
)
from copilot.tools.registry import ToolRegistry

ACCOUNTS_PAYABLE_PLAN_ID = "accounts-payable-analysis-v1"
ACCOUNTS_PAYABLE_PLAN_VERSION = 1

RETRIEVE_AP_POLICY = "retrieve-ap-policy"
AGGREGATE_EXCEPTION_SUMMARY = "analyze-ap-exception-summary"
AGGREGATE_SUPPLIER_RATE = "analyze-ap-supplier-exception-rate"
GENERATE_AP_REPORT = "generate-accounts-payable-report"


@dataclass(frozen=True, slots=True)
class APDetectionPlanBinding:
    """One requested exception mapped to its frozen dataset and operation."""

    exception_types: tuple[APExceptionType, ...]
    database_template: APDatabaseTemplate
    operation: APAnalyticsOperation
    database_suffix: str
    analysis_suffix: str


AP_DETECTION_BINDINGS: tuple[APDetectionPlanBinding, ...] = (
    APDetectionPlanBinding(
        (APExceptionType.EXACT_DUPLICATE_INVOICE,),
        APDatabaseTemplate.DUPLICATE_CANDIDATES,
        APAnalyticsOperation.EXACT_DUPLICATE_INVOICE_DETECTION,
        "query-ap-duplicate-candidates",
        "analyze-ap-exact-duplicates",
    ),
    APDetectionPlanBinding(
        (APExceptionType.PO_AMOUNT_VARIANCE,),
        APDatabaseTemplate.INVOICE_PO_VARIANCE,
        APAnalyticsOperation.INVOICE_PO_VARIANCE_DETECTION,
        "query-ap-invoice-po-variance",
        "analyze-ap-invoice-po-variance",
    ),
    APDetectionPlanBinding(
        (APExceptionType.MISSING_REQUIRED_PO,),
        APDatabaseTemplate.INVOICE_PO_VARIANCE,
        APAnalyticsOperation.MISSING_PO_DETECTION,
        "query-ap-invoice-po-variance",
        "analyze-ap-missing-po",
    ),
    APDetectionPlanBinding(
        (APExceptionType.LATE_PAYMENT, APExceptionType.MATERIAL_EARLY_PAYMENT),
        APDatabaseTemplate.PAYMENT_TERMS,
        APAnalyticsOperation.PAYMENT_TERM_COMPLIANCE_DETECTION,
        "query-ap-payment-terms",
        "analyze-ap-payment-terms",
    ),
    APDetectionPlanBinding(
        (APExceptionType.OVERPAYMENT,),
        APDatabaseTemplate.PAYMENT_AMOUNT,
        APAnalyticsOperation.OVERPAYMENT_DETECTION,
        "query-ap-payment-amount",
        "analyze-ap-overpayment",
    ),
)

AP_POPULATION_SUFFIX = "query-ap-invoice-population"


def ap_step_id(task_id: str, suffix: str) -> str:
    """Bind a canonical AP operation suffix to one immutable Task identifier."""
    return f"{task_id}:{suffix}"


def selected_ap_detection_bindings(
    constraints: AccountsPayableConstraintsV1,
) -> tuple[APDetectionPlanBinding, ...]:
    """Return each selected operation once in the frozen deterministic order."""
    requested = set(constraints.exception_types)
    return tuple(
        binding
        for binding in AP_DETECTION_BINDINGS
        if requested.intersection(binding.exception_types)
    )


def ap_database_template_for_step(step_identifier: str) -> APDatabaseTemplate:
    """Resolve only canonical AP database step suffixes to approved templates."""
    suffix = step_identifier.rsplit(":", 1)[-1]
    if suffix == AP_POPULATION_SUFFIX:
        return APDatabaseTemplate.INVOICE_POPULATION
    for binding in AP_DETECTION_BINDINGS:
        if suffix == binding.database_suffix:
            return binding.database_template
    raise ValueError("Step is not a canonical Accounts Payable database query")


def ap_analytics_operation_for_step(step_identifier: str) -> APAnalyticsOperation:
    """Resolve only canonical AP analysis step suffixes to approved operations."""
    suffix = step_identifier.rsplit(":", 1)[-1]
    if suffix == AGGREGATE_EXCEPTION_SUMMARY:
        return APAnalyticsOperation.EXCEPTION_SUMMARY
    if suffix == AGGREGATE_SUPPLIER_RATE:
        return APAnalyticsOperation.SUPPLIER_EXCEPTION_RATE
    for binding in AP_DETECTION_BINDINGS:
        if suffix == binding.analysis_suffix:
            return binding.operation
    raise ValueError("Step is not a canonical Accounts Payable analytics operation")


class AccountsPayableAnalysisPlanFactory:
    """Create the bounded AP DAG strictly from the validated exception selection."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def create(self, request: TaskRequest, contract: TaskContract) -> TaskPlan:
        """Create one task-bound AP plan without granting the model template authority."""
        del request
        if not isinstance(contract.constraints, AccountsPayableConstraintsV1):
            raise ValueError("Accounts Payable plan requires AP constraints")
        task_id = contract.task_id
        selected = selected_ap_detection_bindings(contract.constraints)
        knowledge = self._step(
            task_id,
            RETRIEVE_AP_POLICY,
            StepType.KNOWLEDGE_SEARCH,
            CapabilityName.KNOWLEDGE_SEARCH,
            (),
            _read_retry("KNOWLEDGE"),
        )
        population = self._step(
            task_id,
            AP_POPULATION_SUFFIX,
            StepType.DATABASE_QUERY,
            CapabilityName.DATABASE_QUERY,
            (),
            _read_retry("DATABASE"),
        )
        database_steps: dict[APDatabaseTemplate, TaskStep] = {
            APDatabaseTemplate.INVOICE_POPULATION: population
        }
        for binding in selected:
            if binding.database_template not in database_steps:
                database_steps[binding.database_template] = self._step(
                    task_id,
                    binding.database_suffix,
                    StepType.DATABASE_QUERY,
                    CapabilityName.DATABASE_QUERY,
                    (),
                    _read_retry("DATABASE"),
                )
        detections = tuple(
            self._step(
                task_id,
                binding.analysis_suffix,
                StepType.ANALYSIS,
                CapabilityName.ANALYSIS_ENGINE,
                (
                    population.step_id,
                    database_steps[binding.database_template].step_id,
                ),
                _analysis_retry(),
            )
            for binding in selected
        )
        detection_ids = tuple(step.step_id for step in detections)
        summary = self._step(
            task_id,
            AGGREGATE_EXCEPTION_SUMMARY,
            StepType.ANALYSIS,
            CapabilityName.ANALYSIS_ENGINE,
            (population.step_id, *detection_ids),
            _analysis_retry(),
        )
        supplier_rate = self._step(
            task_id,
            AGGREGATE_SUPPLIER_RATE,
            StepType.ANALYSIS,
            CapabilityName.ANALYSIS_ENGINE,
            (population.step_id, *detection_ids),
            _analysis_retry(),
        )
        report = self._step(
            task_id,
            GENERATE_AP_REPORT,
            StepType.REPORT_GENERATION,
            CapabilityName.REPORT_GENERATOR,
            (knowledge.step_id, summary.step_id, supplier_rate.step_id),
            _report_retry(),
        )
        return TaskPlan(
            task_id=task_id,
            steps=(
                knowledge,
                *database_steps.values(),
                *detections,
                summary,
                supplier_rate,
                report,
            ),
            planning_version=ACCOUNTS_PAYABLE_PLAN_VERSION,
        )

    def _step(
        self,
        task_id: str,
        suffix: str,
        step_type: StepType,
        capability: CapabilityName,
        dependencies: tuple[str, ...],
        retry_policy: RetryPolicy,
    ) -> TaskStep:
        profile = ACCOUNTS_PAYABLE_CONTRACT_PROFILES[capability]
        definition = self._registry.profile_registration(capability.value, profile).tool.definition
        return TaskStep(
            step_id=ap_step_id(task_id, suffix),
            task_id=task_id,
            step_type=step_type,
            tool_name=definition.tool_name,
            tool_version=definition.tool_version,
            contract_profile=profile,
            input_schema=definition.input_schema,
            output_schema=definition.output_schema,
            dependency=dependencies,
            retry_policy=retry_policy,
        )


def _read_retry(prefix: str) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=3,
        backoff_seconds=(1, 2),
        retryable_error_codes=(f"{prefix}_UNAVAILABLE", f"{prefix}_TIMEOUT"),
    )


def _analysis_retry() -> RetryPolicy:
    return RetryPolicy(
        max_attempts=2,
        backoff_seconds=(1,),
        retryable_error_codes=("ANALYSIS_ENGINE_FAILURE", "ANALYSIS_TIMEOUT"),
    )


def _report_retry() -> RetryPolicy:
    return RetryPolicy(
        max_attempts=2,
        backoff_seconds=(1,),
        retryable_error_codes=("REPORT_GENERATION_FAILURE", "REPORT_TIMEOUT"),
    )


__all__ = [
    "ACCOUNTS_PAYABLE_PLAN_ID",
    "AGGREGATE_EXCEPTION_SUMMARY",
    "AGGREGATE_SUPPLIER_RATE",
    "AP_DETECTION_BINDINGS",
    "AP_POPULATION_SUFFIX",
    "AccountsPayableAnalysisPlanFactory",
    "GENERATE_AP_REPORT",
    "RETRIEVE_AP_POLICY",
    "ap_analytics_operation_for_step",
    "ap_database_template_for_step",
    "ap_step_id",
    "selected_ap_detection_bindings",
]
