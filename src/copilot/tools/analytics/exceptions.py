"""Safe typed failures raised by the v1.0 Analytics Tool."""

from copilot.contracts import ErrorType
from copilot.tools.exceptions import ToolRuntimeError


class AnalyticsInputError(ToolRuntimeError):
    """Raised when dataset fields or units violate the frozen metric specification."""

    def __init__(self, message: str = "Analytics input does not match quality_metrics.v1") -> None:
        super().__init__(
            error_code="ANALYSIS_INPUT_INVALID",
            error_type=ErrorType.VALIDATION,
            message=message,
        )


class AnalyticsInputDeniedError(ToolRuntimeError):
    """Raised when evidence ownership, type, or checksum cannot be trusted."""

    def __init__(self, message: str = "Analytics input evidence is not authorized") -> None:
        super().__init__(
            error_code="ANALYSIS_INPUT_DENIED",
            error_type=ErrorType.PERMISSION,
            message=message,
        )


class AnalyticsResultError(ToolRuntimeError):
    """Raised when a computed result violates deterministic output invariants."""

    def __init__(self, message: str = "Analytics result failed deterministic validation") -> None:
        super().__init__(
            error_code="ANALYSIS_ENGINE_FAILURE",
            error_type=ErrorType.TECHNICAL,
            message=message,
            recoverable=False,
        )


__all__ = [
    "AnalyticsInputDeniedError",
    "AnalyticsInputError",
    "AnalyticsResultError",
]
