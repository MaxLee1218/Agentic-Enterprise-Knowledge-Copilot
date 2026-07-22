"""Typed, safe failures raised at tool runtime boundaries."""

from copilot.contracts import DomainError, ErrorType, TaskError


class ToolRuntimeError(DomainError):
    """Base exception carrying a safe TaskError for a runtime boundary failure."""

    def __init__(
        self,
        *,
        error_code: str,
        error_type: ErrorType,
        message: str,
        recoverable: bool = False,
    ) -> None:
        super().__init__(
            TaskError(
                error_code=error_code,
                error_type=error_type,
                message=message,
                recoverable=recoverable,
            )
        )


class ToolAlreadyExistsError(ToolRuntimeError):
    """Raised when a name is already bound in a registry."""

    def __init__(self, name: str) -> None:
        super().__init__(
            error_code="TOOL_ALREADY_EXISTS",
            error_type=ErrorType.VALIDATION,
            message=f"Tool '{name}' is already registered",
        )


class ToolNotFoundError(ToolRuntimeError):
    """Raised when a requested registered capability does not exist."""

    def __init__(self, name: str) -> None:
        super().__init__(
            error_code="TOOL_NOT_FOUND",
            error_type=ErrorType.VALIDATION,
            message=f"Tool '{name}' is not registered",
        )


class ToolDefinitionValidationError(ToolRuntimeError):
    """Raised when a plugin definition is unsafe or malformed."""

    def __init__(self, message: str) -> None:
        super().__init__(
            error_code="TOOL_DEFINITION_INVALID",
            error_type=ErrorType.VALIDATION,
            message=message,
        )


class ToolValidationError(ToolRuntimeError):
    """Raised when a call does not match its registered invocation contract."""

    def __init__(self, message: str) -> None:
        super().__init__(
            error_code="TOOL_INPUT_INVALID",
            error_type=ErrorType.VALIDATION,
            message=message,
        )


class ToolAuthorizationError(ToolRuntimeError):
    """Raised by policy implementations when a call is not authorized."""

    def __init__(self, message: str = "Tool invocation is not authorized") -> None:
        super().__init__(
            error_code="TOOL_ACCESS_DENIED",
            error_type=ErrorType.PERMISSION,
            message=message,
        )


class ToolTimeoutError(ToolRuntimeError):
    """Internal signal that a tool exceeded its invocation deadline."""

    def __init__(
        self,
        *,
        error_code: str = "TOOL_TIMEOUT",
        message: str = "Tool execution timed out",
    ) -> None:
        super().__init__(
            error_code=error_code,
            error_type=ErrorType.TIMEOUT,
            message=message,
            recoverable=True,
        )


class ToolExecutionError(ToolRuntimeError):
    """Safe technical failure raised by a tool adapter or runner."""

    def __init__(
        self,
        *,
        error_code: str = "TOOL_EXECUTION_FAILED",
        message: str = "Tool execution failed",
        recoverable: bool = True,
    ) -> None:
        super().__init__(
            error_code=error_code,
            error_type=ErrorType.TECHNICAL,
            message=message,
            recoverable=recoverable,
        )


class ToolBusinessError(ToolRuntimeError):
    """Safe non-retryable business outcome raised by an adapter."""

    def __init__(self, *, error_code: str, message: str) -> None:
        super().__init__(
            error_code=error_code,
            error_type=ErrorType.BUSINESS,
            message=message,
        )


class ToolPermissionError(ToolRuntimeError):
    """Safe non-retryable permission outcome raised by an adapter."""

    def __init__(self, *, error_code: str, message: str) -> None:
        super().__init__(
            error_code=error_code,
            error_type=ErrorType.PERMISSION,
            message=message,
        )


class ToolEvidenceError(ToolRuntimeError):
    """Raised when successful output cannot be registered as required evidence."""

    def __init__(self) -> None:
        super().__init__(
            error_code="TOOL_EVIDENCE_RECORDING_FAILED",
            error_type=ErrorType.TECHNICAL,
            message="Tool evidence could not be recorded",
        )


class ToolAuditError(ToolRuntimeError):
    """Raised when the immutable audit record cannot be committed."""

    def __init__(self) -> None:
        super().__init__(
            error_code="TOOL_AUDIT_RECORDING_FAILED",
            error_type=ErrorType.TECHNICAL,
            message="Tool audit record could not be recorded",
        )
