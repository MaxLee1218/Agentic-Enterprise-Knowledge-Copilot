"""Typed workflow application failures."""


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
