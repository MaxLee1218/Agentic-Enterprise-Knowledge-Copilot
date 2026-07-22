"""Governed lifecycle executor for registered enterprise tool plugins."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from copilot.contracts import (
    ErrorType,
    JsonObject,
    TaskError,
    ToolCall,
    ToolDefinition,
    ToolResult,
    ToolResultStatus,
)
from copilot.contracts.validators import utc_now
from copilot.tools.base import (
    EvidenceRecorder,
    ToolAuditRecord,
    ToolAuditSink,
    ToolAuthorizer,
    ToolExecutionContext,
    ToolRunner,
)
from copilot.tools.exceptions import (
    ToolAuditError,
    ToolAuthorizationError,
    ToolRuntimeError,
    ToolTimeoutError,
    ToolValidationError,
)
from copilot.tools.registry import ToolRegistry
from copilot.tools.runner import ThreadPoolToolRunner
from copilot.tools.schema import validate_payload


class ToolExecutor:
    """Execute registered tools without knowing any concrete business capability."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        authorizer: ToolAuthorizer,
        evidence_recorder: EvidenceRecorder,
        audit_sink: ToolAuditSink,
        runner: ToolRunner | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._registry = registry
        self._authorizer = authorizer
        self._evidence_recorder = evidence_recorder
        self._audit_sink = audit_sink
        if runner is None:
            self._owned_runner: ThreadPoolToolRunner | None = ThreadPoolToolRunner()
            self._runner: ToolRunner = self._owned_runner
        else:
            self._owned_runner = None
            self._runner = runner
        self._clock = clock

    def execute(self, call: ToolCall, *, attempt: int = 1) -> ToolResult:
        """Run one governed attempt through validation, policy, evidence, and audit."""
        if not 1 <= attempt <= 3:
            raise ValueError("attempt must be between 1 and 3")
        tool = self._registry.get(call.tool_name)
        started_at = self._clock()
        self._validate_call(call, tool.definition, started_at, attempt)

        try:
            self._authorizer.authorize(call, tool.definition)
        except ToolAuthorizationError as exc:
            return self._failure_result(
                call=call,
                started_at=started_at,
                status=ToolResultStatus.PERMISSION_DENIED,
                error=self._bind_error(exc.error, call),
                attempt=attempt,
            )
        except Exception:
            return self._failure_result(
                call=call,
                started_at=started_at,
                status=ToolResultStatus.PERMISSION_DENIED,
                error=self._new_error(
                    call,
                    error_code="TOOL_POLICY_UNAVAILABLE",
                    error_type=ErrorType.PERMISSION,
                    message="Tool invocation could not be authorized",
                ),
                attempt=attempt,
            )

        timeout_seconds = min(
            float(tool.definition.timeout.attempt_seconds),
            (call.deadline_at - started_at).total_seconds(),
        )
        if timeout_seconds <= 0:
            return self._timeout_result(call, started_at, attempt)

        try:
            payload = self._runner.run(
                tool,
                call.input,
                ToolExecutionContext(call=call, metadata=JsonObject({"attempt": attempt})),
                timeout_seconds,
            )
        except ToolTimeoutError as exc:
            return self._failure_result(
                call=call,
                started_at=started_at,
                status=ToolResultStatus.TIMEOUT,
                error=self._bind_error(exc.error, call),
                attempt=attempt,
            )
        except ToolRuntimeError as exc:
            return self._failure_result(
                call=call,
                started_at=started_at,
                status=_status_for_error(exc.error),
                error=self._bind_error(exc.error, call),
                attempt=attempt,
            )

        try:
            validate_payload(payload.output, tool.definition.output_schema.root, "output")
        except ToolValidationError:
            return self._failure_result(
                call=call,
                started_at=started_at,
                status=ToolResultStatus.TECHNICAL_FAILURE,
                error=self._new_error(
                    call,
                    error_code="TOOL_OUTPUT_INVALID",
                    error_type=ErrorType.VALIDATION,
                    message="Tool output failed its registered schema",
                ),
                attempt=attempt,
            )

        try:
            evidence = self._evidence_recorder.record(call, payload.evidence)
        except Exception:
            return self._failure_result(
                call=call,
                started_at=started_at,
                status=ToolResultStatus.TECHNICAL_FAILURE,
                error=self._new_error(
                    call,
                    error_code="TOOL_EVIDENCE_RECORDING_FAILED",
                    error_type=ErrorType.TECHNICAL,
                    message="Tool evidence could not be recorded",
                ),
                attempt=attempt,
            )

        completed_at = self._clock()
        result = ToolResult(
            tool_call_id=call.tool_call_id,
            task_id=call.task_id,
            step_id=call.step_id,
            tool_name=call.tool_name,
            tool_version=call.tool_version,
            status=ToolResultStatus.SUCCESS,
            output=payload.output,
            error=None,
            started_at=started_at,
            completed_at=completed_at,
            attempt=attempt,
            evidence_ids=tuple(item.evidence_id for item in evidence),
        )
        self._audit(result)
        return result

    def close(self) -> None:
        """Close an internally owned runner."""
        if self._owned_runner is not None:
            self._owned_runner.close()

    def __enter__(self) -> ToolExecutor:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _validate_call(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        started_at: datetime,
        attempt: int,
    ) -> None:
        try:
            if (
                call.tool_name != definition.tool_name
                or call.tool_version != definition.tool_version
            ):
                raise ToolValidationError("Tool call does not match the registered definition")
            validate_payload(call.input, definition.input_schema.root, "input")
        except ToolValidationError as exc:
            completed_at = self._clock()
            self._append_audit(
                ToolAuditRecord(
                    tool_call_id=call.tool_call_id,
                    task_id=call.task_id,
                    step_id=call.step_id,
                    tool_name=call.tool_name,
                    tool_version=call.tool_version,
                    status=ToolResultStatus.BUSINESS_FAILURE,
                    latency_ms=_latency_ms(started_at, completed_at),
                    timestamp=completed_at,
                    attempt=attempt,
                    error_code=exc.error.error_code,
                )
            )
            raise

    def _timeout_result(self, call: ToolCall, started_at: datetime, attempt: int) -> ToolResult:
        return self._failure_result(
            call=call,
            started_at=started_at,
            status=ToolResultStatus.TIMEOUT,
            error=self._new_error(
                call,
                error_code="TOOL_TIMEOUT",
                error_type=ErrorType.TIMEOUT,
                message="Tool execution timed out",
                recoverable=True,
            ),
            attempt=attempt,
        )

    def _failure_result(
        self,
        *,
        call: ToolCall,
        started_at: datetime,
        status: ToolResultStatus,
        error: TaskError,
        attempt: int,
    ) -> ToolResult:
        completed_at = self._clock()
        result = ToolResult(
            tool_call_id=call.tool_call_id,
            task_id=call.task_id,
            step_id=call.step_id,
            tool_name=call.tool_name,
            tool_version=call.tool_version,
            status=status,
            output=None,
            error=error,
            started_at=started_at,
            completed_at=completed_at,
            attempt=attempt,
        )
        self._audit(result)
        return result

    def _audit(self, result: ToolResult) -> None:
        self._append_audit(
            ToolAuditRecord(
                tool_call_id=result.tool_call_id,
                task_id=result.task_id,
                step_id=result.step_id,
                tool_name=result.tool_name,
                tool_version=result.tool_version,
                status=result.status,
                latency_ms=result.latency_ms or 0,
                timestamp=result.completed_at,
                attempt=result.attempt,
                error_code=result.error.error_code if result.error is not None else None,
            )
        )

    def _append_audit(self, record: ToolAuditRecord) -> None:
        try:
            self._audit_sink.append(record)
        except Exception as exc:
            raise ToolAuditError() from exc

    @staticmethod
    def _bind_error(error: TaskError, call: ToolCall) -> TaskError:
        return error.model_copy(
            update={
                "task_id": call.task_id,
                "step_id": call.step_id,
                "tool_call_id": call.tool_call_id,
            }
        )

    @staticmethod
    def _new_error(
        call: ToolCall,
        *,
        error_code: str,
        error_type: ErrorType,
        message: str,
        recoverable: bool = False,
    ) -> TaskError:
        return TaskError(
            error_code=error_code,
            error_type=error_type,
            message=message,
            recoverable=recoverable,
            task_id=call.task_id,
            step_id=call.step_id,
            tool_call_id=call.tool_call_id,
        )


def _status_for_error(error: TaskError) -> ToolResultStatus:
    if error.error_type is ErrorType.BUSINESS:
        return ToolResultStatus.BUSINESS_FAILURE
    if error.error_type is ErrorType.PERMISSION:
        return ToolResultStatus.PERMISSION_DENIED
    if error.error_type is ErrorType.TIMEOUT:
        return ToolResultStatus.TIMEOUT
    return ToolResultStatus.TECHNICAL_FAILURE


def _latency_ms(started_at: datetime, completed_at: datetime) -> int:
    return round((completed_at - started_at).total_seconds() * 1000)
