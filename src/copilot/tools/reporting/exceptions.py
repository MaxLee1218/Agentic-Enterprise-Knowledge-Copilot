"""Stable Report Tool failures mapped onto the governed runtime error model."""

from copilot.contracts import ErrorType
from copilot.tools.exceptions import ToolRuntimeError


class ReportInputError(ToolRuntimeError):
    """Raised when a report input or Evidence reference is structurally invalid."""

    def __init__(self, message: str = "Report input is invalid") -> None:
        super().__init__(
            error_code="REPORT_INPUT_INVALID",
            error_type=ErrorType.VALIDATION,
            message=message,
        )


class ReportInputDeniedError(ToolRuntimeError):
    """Raised when report input is outside the trusted Task scope."""

    def __init__(self, message: str = "Report input is not authorized") -> None:
        super().__init__(
            error_code="REPORT_INPUT_DENIED",
            error_type=ErrorType.PERMISSION,
            message=message,
        )


class UnsupportedReportFormatError(ToolRuntimeError):
    """Raised for a format outside the frozen PDF/JSON allowlist."""

    def __init__(self) -> None:
        super().__init__(
            error_code="REPORT_FORMAT_UNSUPPORTED",
            error_type=ErrorType.BUSINESS,
            message="Report format is not supported by the frozen v1.0 contract",
        )


class ReportRenderingError(ToolRuntimeError):
    """Raised when a deterministic renderer cannot create valid bytes."""

    def __init__(self) -> None:
        super().__init__(
            error_code="REPORT_GENERATION_FAILURE",
            error_type=ErrorType.TECHNICAL,
            message="Report rendering failed",
            recoverable=True,
        )


class ReportPersistenceError(ToolRuntimeError):
    """Raised when an Artifact cannot be atomically committed."""

    def __init__(self) -> None:
        super().__init__(
            error_code="REPORT_GENERATION_FAILURE",
            error_type=ErrorType.TECHNICAL,
            message="Report Artifact could not be committed",
            recoverable=True,
        )


class ReportConsistencyError(ToolRuntimeError):
    """Raised when structured or rendered report consistency checks fail."""

    def __init__(self, message: str = "Report consistency validation failed") -> None:
        super().__init__(
            error_code="REPORT_INPUT_INVALID",
            error_type=ErrorType.VALIDATION,
            message=message,
        )


__all__ = [
    "ReportConsistencyError",
    "ReportInputDeniedError",
    "ReportInputError",
    "ReportPersistenceError",
    "ReportRenderingError",
    "UnsupportedReportFormatError",
]
