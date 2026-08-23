"""Safe typed failures raised by governed Analytics Tool profiles."""

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


class APAnalyticsInputError(ToolRuntimeError):
    """Raised when AP operation rows or parameters violate the frozen profile."""

    def __init__(self, message: str = "AP analytics input violates its frozen contract") -> None:
        super().__init__(
            error_code="ANALYSIS_INPUT_INVALID",
            error_type=ErrorType.VALIDATION,
            message=message,
        )


class APAnalyticsInputDeniedError(ToolRuntimeError):
    """Raised when AP dataset, rule, tenant, or Evidence lineage is not trusted."""

    def __init__(self, message: str = "AP analytics input evidence is not authorized") -> None:
        super().__init__(
            error_code="ANALYSIS_INPUT_DENIED",
            error_type=ErrorType.PERMISSION,
            message=message,
        )


class APAnalyticsOperationUnsupportedError(ToolRuntimeError):
    """Raised before schema parsing for an operation outside the seven-item union."""

    def __init__(self) -> None:
        super().__init__(
            error_code="ANALYSIS_OPERATION_UNSUPPORTED",
            error_type=ErrorType.BUSINESS,
            message="Requested AP analytics operation is not supported",
        )


class APAnalyticsDataIncompleteError(ToolRuntimeError):
    """Raised when a non-empty requested operation has no eligible coverage."""

    def __init__(self, message: str = "AP source data has no eligible operation coverage") -> None:
        super().__init__(
            error_code="AP_DATA_INCOMPLETE",
            error_type=ErrorType.BUSINESS,
            message=message,
        )


class APAnalyticsDataConsistencyError(ToolRuntimeError):
    """Raised when tenant or parent relationships cannot support a conclusion."""

    def __init__(self, message: str = "AP source relationships are inconsistent") -> None:
        super().__init__(
            error_code="AP_DATA_INCONSISTENT",
            error_type=ErrorType.VALIDATION,
            message=message,
        )


class APAnalyticsScopeTooLargeError(ToolRuntimeError):
    """Raised when truncation or exception limits make a complete result impossible."""

    def __init__(self, message: str = "AP analytics scope exceeds a frozen hard limit") -> None:
        super().__init__(
            error_code="AP_SCOPE_TOO_LARGE",
            error_type=ErrorType.BUSINESS,
            message=message,
            recoverable=True,
        )


class APPolicyRuleUnavailableError(ToolRuntimeError):
    """Raised when an exact effective controlled rule cannot be resolved."""

    def __init__(self, message: str = "A required AP policy rule is unavailable") -> None:
        super().__init__(
            error_code="POLICY_RULE_UNAVAILABLE",
            error_type=ErrorType.BUSINESS,
            message=message,
        )


class APPolicyRuleBindingMismatchError(ToolRuntimeError):
    """Raised when a manifest or Document Evidence binding drifts."""

    def __init__(self, message: str = "AP policy rule binding does not match Evidence") -> None:
        super().__init__(
            error_code="POLICY_RULE_BINDING_MISMATCH",
            error_type=ErrorType.VALIDATION,
            message=message,
        )


class APPolicyThresholdRelaxationError(ToolRuntimeError):
    """Raised when requested/effective materiality weakens the organization rule."""

    def __init__(self) -> None:
        super().__init__(
            error_code="POLICY_THRESHOLD_RELAXATION_ATTEMPT",
            error_type=ErrorType.PERMISSION,
            message="Requested AP materiality would relax the controlled threshold",
        )


__all__ = [
    "APAnalyticsDataConsistencyError",
    "APAnalyticsDataIncompleteError",
    "APAnalyticsInputDeniedError",
    "APAnalyticsInputError",
    "APAnalyticsOperationUnsupportedError",
    "APAnalyticsScopeTooLargeError",
    "APPolicyRuleBindingMismatchError",
    "APPolicyRuleUnavailableError",
    "APPolicyThresholdRelaxationError",
    "AnalyticsInputDeniedError",
    "AnalyticsInputError",
    "AnalyticsResultError",
]
