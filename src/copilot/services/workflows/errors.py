"""Typed workflow application failures."""

from enum import StrEnum


class WorkflowError(RuntimeError):
    """Base deterministic workflow error."""


class PlanValidationError(WorkflowError):
    """Raised before execution when a fixed plan is not executable."""


class StepInputError(WorkflowError):
    """Raised when a schema-bound step input cannot be constructed."""


class StateTransitionError(WorkflowError):
    """Raised when a state-machine event is not legal."""


class VerificationError(WorkflowError):
    """Raised when final evidence or artifact verification fails."""


class WorkflowRecoveryError(WorkflowError):
    """Raised when durable domain state and a checkpoint cannot be reconciled safely."""


class PlannerErrorCode(StrEnum):
    """Stable root causes for pre-execution planning failures."""

    PROVIDER = "PLANNER_PROVIDER_ERROR"
    TIMEOUT = "PLANNER_TIMEOUT_ERROR"
    INVALID_JSON = "PLANNER_INVALID_JSON_ERROR"
    SCHEMA_VALIDATION = "PLANNER_SCHEMA_VALIDATION_ERROR"
    UNSUPPORTED_CAPABILITY = "PLANNER_UNSUPPORTED_CAPABILITY_ERROR"
    COMPILATION = "PLANNER_COMPILATION_ERROR"
    REPAIR_EXHAUSTED = "PLANNER_REPAIR_EXHAUSTED_ERROR"


class PlannerError(WorkflowError):
    """Safe typed failure raised before a canonical TaskPlan is executable."""

    code = PlannerErrorCode.COMPILATION

    def __init__(self, message: str, *, attempts: int = 1) -> None:
        super().__init__(message)
        self.attempts = attempts


class PlannerProviderError(PlannerError):
    code = PlannerErrorCode.PROVIDER


class PlannerTimeoutError(PlannerError):
    code = PlannerErrorCode.TIMEOUT


class PlannerInvalidJsonError(PlannerError):
    code = PlannerErrorCode.INVALID_JSON


class PlannerSchemaValidationError(PlannerError):
    code = PlannerErrorCode.SCHEMA_VALIDATION


class PlannerUnsupportedCapabilityError(PlannerError):
    code = PlannerErrorCode.UNSUPPORTED_CAPABILITY


class PlannerCompilationError(PlannerError):
    code = PlannerErrorCode.COMPILATION


class PlannerRepairExhaustedError(PlannerError):
    code = PlannerErrorCode.REPAIR_EXHAUSTED
