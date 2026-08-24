"""Governed lifecycle executor for registered enterprise tool plugins."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import datetime
from time import monotonic
from typing import cast

from pydantic import JsonValue

from copilot.contracts import (
    ErrorType,
    JsonObject,
    SpanKind,
    SpanStatus,
    TaskError,
    ToolCall,
    ToolDefinition,
    ToolResult,
    ToolResultStatus,
)
from copilot.contracts.errors import DomainError
from copilot.contracts.validators import utc_now
from copilot.security import ContentSourceType, OutputDisposition, OutputGuard
from copilot.security.prompt_injection import PromptInjectionDetector
from copilot.services.execution import ExecutionContext
from copilot.services.observability import EventName, NoopObservability, ObservabilityPort
from copilot.tools.base import (
    EvidenceRecorder,
    ToolAuditRecord,
    ToolAuditSink,
    ToolAuthorizer,
    ToolExecutionContext,
    ToolRunner,
)
from copilot.tools.cancellation import InvocationCancellationRegistry
from copilot.tools.exceptions import (
    ToolAuditError,
    ToolAuthorizationError,
    ToolCancellationError,
    ToolRuntimeError,
    ToolTimeoutError,
    ToolValidationError,
)
from copilot.tools.registry import ToolRegistry
from copilot.tools.runner import ThreadPoolToolRunner
from copilot.tools.schema import validate_payload

_TOOL_TIMEOUT_DETAILS = {
    "knowledge_search": ("KNOWLEDGE_TIMEOUT", "Enterprise knowledge retrieval timed out"),
    "database_query": ("DATABASE_TIMEOUT", "Database query timed out"),
    "analysis_engine": ("ANALYSIS_TIMEOUT", "Analysis execution timed out"),
    "report_generator": ("REPORT_TIMEOUT", "Report generation timed out"),
}


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
        output_guard: OutputGuard | None = None,
        injection_detector: PromptInjectionDetector | None = None,
        observability: ObservabilityPort | None = None,
        timer: Callable[[], float] = monotonic,
        max_step_duration_seconds: float = 60,
        max_database_rows: int = 10_000,
        cancellation_registry: InvocationCancellationRegistry | None = None,
    ) -> None:
        if max_step_duration_seconds <= 0 or max_database_rows <= 0:
            raise ValueError("tool performance limits must be positive")
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
        self._output_guard = output_guard or OutputGuard()
        self._injection_detector = injection_detector or PromptInjectionDetector()
        self._observability = observability or NoopObservability()
        self._timer = timer
        self._max_step_duration_seconds = max_step_duration_seconds
        self._max_database_rows = max_database_rows
        self._cancellations = cancellation_registry or InvocationCancellationRegistry()

    def execute(
        self,
        call: ToolCall,
        execution_context: ExecutionContext,
        *,
        attempt: int = 1,
    ) -> ToolResult:
        """Run one governed attempt through validation, policy, evidence, and audit."""
        self._validate_execution_context(call, execution_context)
        trace_id = execution_context.trace_id
        labels = {"tool_name": call.tool_name}
        started_tick = self._timer()
        self._cancellations.register(
            call.task_id, call.tool_call_id, execution_context.cancellation
        )
        try:
            with self._observability.bind_context(
                task_id=call.task_id,
                trace_id=trace_id,
                step_id=call.step_id,
                tool_name=call.tool_name,
                tenant_id=execution_context.tenant_id,
                user_id=execution_context.user_id,
            ):
                self._observability.emit(
                    EventName.STEP_STARTED,
                    fields={"status": "RUNNING", "attempt": attempt},
                )
                with self._observability.span(
                    f"step.{call.step_id}",
                    SpanKind.STEP,
                    attributes={"attempt": attempt},
                ) as step_span:
                    self._observability.emit(
                        EventName.TOOL_RETRY if attempt > 1 else EventName.TOOL_STARTED,
                        level=logging.WARNING if attempt > 1 else logging.INFO,
                        fields={"status": "RUNNING", "attempt": attempt},
                    )
                    self._observability.increment("tool_executions_total", labels=labels)
                    self._observability.gauge_add("active_tool_calls", 1, labels=labels)
                    if attempt > 1:
                        self._observability.increment("tool_retries_total", labels=labels)
                    try:
                        with self._observability.span(
                            f"tool.{call.tool_name}",
                            SpanKind.TOOL,
                            attributes={"attempt": attempt, "tool_version": call.tool_version},
                        ) as tool_span:
                            try:
                                limited = self._database_limit_result(
                                    call, attempt, execution_context
                                )
                                result = limited or self._execute_attempt(
                                    call,
                                    attempt=attempt,
                                    execution_context=execution_context,
                                )
                            except BaseException as exc:
                                latency_ms = max(0.0, (self._timer() - started_tick) * 1000)
                                tool_span.set_status(
                                    SpanStatus.FAILED,
                                    error_type=type(exc).__name__,
                                )
                                step_span.set_status(
                                    SpanStatus.FAILED,
                                    error_type=type(exc).__name__,
                                )
                                self._observability.increment("tool_failures_total", labels=labels)
                                self._observe_tool_completion(
                                    call,
                                    attempt,
                                    latency_ms,
                                    event=EventName.TOOL_FAILED,
                                    status="FAILED",
                                    error_type=type(exc).__name__,
                                )
                                raise
                            latency_ms = max(0.0, (self._timer() - started_tick) * 1000)
                            status, span_status, event = _observability_status(result.status)
                            error_type = (
                                result.error.error_code if result.error is not None else None
                            )
                            tool_span.set_attribute("evidence_count", len(result.evidence_ids))
                            tool_span.set_attribute(
                                "output_size",
                                (
                                    len(result.output.model_dump_json())
                                    if result.output is not None
                                    else 0
                                ),
                            )
                            tool_span.set_status(span_status, error_type=error_type)
                            step_span.set_status(span_status, error_type=error_type)
                            if result.status is ToolResultStatus.SUCCESS:
                                self._observability.increment("tool_successes_total", labels=labels)
                            elif result.status is ToolResultStatus.TIMEOUT:
                                self._observability.increment("tool_timeouts_total", labels=labels)
                            else:
                                self._observability.increment("tool_failures_total", labels=labels)
                            if error_type is not None and "LIMIT_EXCEEDED" in error_type:
                                self._observability.increment(
                                    "performance_limit_exceeded_total",
                                    labels={"error_type": error_type},
                                )
                                self._observability.emit(
                                    EventName.PERFORMANCE_LIMIT_EXCEEDED,
                                    level=logging.ERROR,
                                    fields={
                                        "error_type": error_type,
                                        "latency_ms": latency_ms,
                                    },
                                )
                            self._observe_tool_completion(
                                call,
                                attempt,
                                latency_ms,
                                event=event,
                                status=status,
                                error_type=error_type,
                            )
                            return result
                    finally:
                        self._observability.gauge_add("active_tool_calls", -1, labels=labels)
        finally:
            self._cancellations.release(call.task_id, call.tool_call_id)

    def _execute_attempt(
        self,
        call: ToolCall,
        *,
        attempt: int = 1,
        execution_context: ExecutionContext,
    ) -> ToolResult:
        """Execute the existing governed lifecycle beneath uniform instrumentation."""
        if not 1 <= attempt <= 3:
            raise ValueError("attempt must be between 1 and 3")
        started_at = self._clock()
        try:
            tool = self._registry.get_version(call.tool_name, call.tool_version)
        except ToolRuntimeError:
            self._append_audit(
                ToolAuditRecord(
                    tool_call_id=call.tool_call_id,
                    task_id=call.task_id,
                    tenant_id=execution_context.tenant_id,
                    trace_id=execution_context.trace_id,
                    step_id=call.step_id,
                    tool_name=call.tool_name,
                    tool_version=call.tool_version,
                    status=ToolResultStatus.PERMISSION_DENIED,
                    latency_ms=0,
                    timestamp=started_at,
                    attempt=attempt,
                    error_code="TOOL_NOT_ALLOWED",
                    principal_id=call.user_id,
                    scopes=execution_context.scopes,
                    purpose=execution_context.purpose,
                    approval_id=call.approval_id,
                    arguments_hash=_arguments_hash(call.input),
                    policy_decision="DENY",
                    reason_code="TOOL_NOT_ALLOWED",
                )
            )
            raise
        self._validate_call(call, tool.definition, started_at, attempt, execution_context)

        try:
            self._authorizer.authorize_with_context(call, tool.definition, execution_context)
        except ToolAuthorizationError as exc:
            return self._failure_result(
                call=call,
                started_at=started_at,
                status=ToolResultStatus.PERMISSION_DENIED,
                error=self._bind_error(exc.error, call),
                attempt=attempt,
                execution_context=execution_context,
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
                execution_context=execution_context,
            )

        try:
            execution_context.cancellation.raise_if_requested()
        except ToolCancellationError as exc:
            return self._cancellation_result(
                call, started_at, attempt, execution_context, exc.error.message
            )

        timeout_seconds = min(
            float(tool.definition.timeout.attempt_seconds),
            self._max_step_duration_seconds,
            (call.deadline_at - started_at).total_seconds(),
        )
        if timeout_seconds <= 0:
            return self._timeout_result(call, started_at, attempt, execution_context)

        try:
            payload = self._runner.run(
                tool,
                call.input,
                ToolExecutionContext(
                    call=call,
                    trace_id=execution_context.trace_id,
                    tenant_id=execution_context.tenant_id,
                    user_id=execution_context.user_id,
                    roles=execution_context.roles,
                    scopes=execution_context.scopes,
                    purpose=execution_context.purpose,
                    cancellation=execution_context.cancellation,
                    metadata=JsonObject(
                        {
                            "attempt": attempt,
                            "trace_id": execution_context.trace_id,
                            "roles": list(execution_context.roles),
                            "scopes": list(execution_context.scopes),
                            "is_demo_identity": execution_context.is_demo_identity,
                            "purpose": execution_context.purpose,
                        }
                    ),
                ),
                timeout_seconds,
            )
        except ToolTimeoutError:
            return self._failure_result(
                call=call,
                started_at=started_at,
                status=ToolResultStatus.TIMEOUT,
                error=self._timeout_error(call),
                attempt=attempt,
                execution_context=execution_context,
            )
        except ToolCancellationError as exc:
            return self._cancellation_result(
                call, started_at, attempt, execution_context, exc.error.message
            )
        except ToolRuntimeError as exc:
            return self._failure_result(
                call=call,
                started_at=started_at,
                status=_status_for_error(exc.error),
                error=self._bind_error(exc.error, call),
                attempt=attempt,
                execution_context=execution_context,
            )

        if not execution_context.cancellation.mark_completed():
            execution_context.cancellation.mark_cancelled()
            return self._cancellation_result(
                call,
                started_at,
                attempt,
                execution_context,
                execution_context.cancellation.reason or "Tool invocation was cancelled",
            )

        safe_output, finding_codes = self._guard_tool_output(
            payload.output,
            tool_name=call.tool_name,
            source_id=call.tool_call_id,
        )
        if safe_output is None:
            return self._failure_result(
                call=call,
                started_at=started_at,
                status=ToolResultStatus.PERMISSION_DENIED,
                error=self._new_error(
                    call,
                    error_code="SENSITIVE_OUTPUT_BLOCKED",
                    error_type=ErrorType.PERMISSION,
                    message="Tool output was blocked by the output safety policy",
                ),
                attempt=attempt,
                execution_context=execution_context,
                security_finding_codes=finding_codes,
            )

        try:
            validate_payload(safe_output, tool.definition.output_schema.root, "output")
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
                execution_context=execution_context,
            )

        try:
            evidence = self._evidence_recorder.record(call, payload.evidence)
        except DomainError as exc:
            security_code = exc.error.error_code
            security_failure = security_code in {"SECRET_DETECTED", "SENSITIVE_OUTPUT_BLOCKED"}
            return self._failure_result(
                call=call,
                started_at=started_at,
                status=(
                    ToolResultStatus.PERMISSION_DENIED
                    if security_failure
                    else ToolResultStatus.TECHNICAL_FAILURE
                ),
                error=self._bind_error(exc.error, call),
                attempt=attempt,
                execution_context=execution_context,
                security_finding_codes=((security_code,) if security_failure else ()),
            )
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
                execution_context=execution_context,
            )

        completed_at = self._clock()
        result = ToolResult(
            tool_call_id=call.tool_call_id,
            task_id=call.task_id,
            step_id=call.step_id,
            tool_name=call.tool_name,
            tool_version=call.tool_version,
            status=ToolResultStatus.SUCCESS,
            output=safe_output,
            error=None,
            started_at=started_at,
            completed_at=completed_at,
            attempt=attempt,
            evidence_ids=tuple(item.evidence_id for item in evidence),
        )
        self._audit(
            result,
            execution_context=execution_context,
            arguments_hash=_arguments_hash(call.input),
            security_finding_codes=finding_codes,
        )
        return result

    def _database_limit_result(
        self, call: ToolCall, attempt: int, execution_context: ExecutionContext
    ) -> ToolResult | None:
        if call.tool_name != "database_query":
            return None
        row_limit = call.input.root.get("row_limit")
        if not isinstance(row_limit, int) or isinstance(row_limit, bool):
            return None
        if row_limit <= self._max_database_rows:
            return None
        return self._failure_result(
            call=call,
            started_at=self._clock(),
            status=ToolResultStatus.BUSINESS_FAILURE,
            error=self._new_error(
                call,
                error_code="DATABASE_ROW_LIMIT_EXCEEDED",
                error_type=ErrorType.VALIDATION,
                message="Database row limit exceeds the configured performance boundary",
            ),
            attempt=attempt,
            execution_context=execution_context,
        )

    def _observe_tool_completion(
        self,
        call: ToolCall,
        attempt: int,
        latency_ms: float,
        *,
        event: str,
        status: str,
        error_type: str | None,
    ) -> None:
        labels = {"tool_name": call.tool_name}
        self._observability.observe("tool_latency_ms", latency_ms, labels=labels)
        self._observability.observe("step_latency_ms", latency_ms, labels=labels)
        self._observability.emit(
            event,
            level=logging.INFO if status == "SUCCEEDED" else logging.ERROR,
            fields={
                "status": status,
                "latency_ms": latency_ms,
                "attempt": attempt,
                "retry_count": max(0, attempt - 1),
                "error_type": error_type,
            },
        )
        self._observability.emit(
            EventName.STEP_COMPLETED if status == "SUCCEEDED" else EventName.STEP_FAILED,
            level=logging.INFO if status == "SUCCEEDED" else logging.ERROR,
            fields={
                "status": status,
                "latency_ms": latency_ms,
                "attempt": attempt,
                "error_type": error_type,
            },
        )

    def close(self) -> None:
        """Close an internally owned runner."""
        self._cancellations.cancel_all(reason="Tool executor is shutting down")
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
        execution_context: ExecutionContext,
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
                    tenant_id=execution_context.tenant_id,
                    trace_id=execution_context.trace_id,
                    step_id=call.step_id,
                    tool_name=call.tool_name,
                    tool_version=call.tool_version,
                    status=ToolResultStatus.BUSINESS_FAILURE,
                    latency_ms=_latency_ms(started_at, completed_at),
                    timestamp=completed_at,
                    attempt=attempt,
                    error_code=exc.error.error_code,
                    principal_id=call.user_id,
                    scopes=execution_context.scopes,
                    purpose=execution_context.purpose,
                    approval_id=call.approval_id,
                    arguments_hash=_arguments_hash(call.input),
                    policy_decision="DENY",
                    reason_code=exc.error.error_code,
                )
            )
            raise

    def _timeout_result(
        self,
        call: ToolCall,
        started_at: datetime,
        attempt: int,
        execution_context: ExecutionContext,
    ) -> ToolResult:
        return self._failure_result(
            call=call,
            started_at=started_at,
            status=ToolResultStatus.TIMEOUT,
            error=self._timeout_error(call),
            attempt=attempt,
            execution_context=execution_context,
        )

    def _timeout_error(
        self,
        call: ToolCall,
    ) -> TaskError:
        error_code, message = _TOOL_TIMEOUT_DETAILS.get(
            call.tool_name,
            ("TOOL_TIMEOUT", "Tool execution timed out"),
        )
        return self._new_error(
            call,
            error_code=error_code,
            error_type=ErrorType.TIMEOUT,
            message=message,
            recoverable=True,
        )

    def _cancellation_result(
        self,
        call: ToolCall,
        started_at: datetime,
        attempt: int,
        execution_context: ExecutionContext,
        message: str,
    ) -> ToolResult:
        return self._failure_result(
            call=call,
            started_at=started_at,
            status=ToolResultStatus.TECHNICAL_FAILURE,
            error=self._new_error(
                call,
                error_code="TOOL_CANCELLED",
                error_type=ErrorType.CANCELLATION,
                message=message,
            ),
            attempt=attempt,
            execution_context=execution_context,
        )

    def _failure_result(
        self,
        *,
        call: ToolCall,
        started_at: datetime,
        status: ToolResultStatus,
        error: TaskError,
        attempt: int,
        execution_context: ExecutionContext,
        security_finding_codes: tuple[str, ...] = (),
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
        self._audit(
            result,
            execution_context=execution_context,
            arguments_hash=_arguments_hash(call.input),
            security_finding_codes=security_finding_codes,
        )
        return result

    def _audit(
        self,
        result: ToolResult,
        *,
        execution_context: ExecutionContext,
        arguments_hash: str,
        security_finding_codes: tuple[str, ...] = (),
    ) -> None:
        tool_origin, tool_provenance = self._registration_audit_metadata(result.tool_name)
        self._append_audit(
            ToolAuditRecord(
                tool_call_id=result.tool_call_id,
                task_id=result.task_id,
                tenant_id=execution_context.tenant_id,
                trace_id=execution_context.trace_id,
                step_id=result.step_id,
                tool_name=result.tool_name,
                tool_version=result.tool_version,
                status=result.status,
                latency_ms=result.latency_ms or 0,
                timestamp=result.completed_at,
                attempt=result.attempt,
                error_code=result.error.error_code if result.error is not None else None,
                principal_id=execution_context.user_id,
                scopes=execution_context.scopes,
                purpose=execution_context.purpose,
                approval_id=execution_context.approval_id,
                arguments_hash=arguments_hash,
                tool_origin=tool_origin,
                tool_provenance=tool_provenance,
                policy_decision=("ALLOW" if result.status is ToolResultStatus.SUCCESS else "DENY"),
                reason_code=(result.error.error_code if result.error is not None else "ALLOWED"),
                security_finding_codes=security_finding_codes,
            )
        )

    def _registration_audit_metadata(self, tool_name: str) -> tuple[str, str]:
        """Return bounded origin/provenance facts without exposing registry objects."""
        try:
            registration = self._registry.registration(tool_name)
        except ToolRuntimeError:
            return "revoked-or-unknown", "unavailable"
        origin = registration.origin
        provenance = registration.provenance
        return (
            f"{origin.origin_type}:{origin.source_id}",
            ":".join(
                value
                for value in (
                    provenance.provider,
                    provenance.revision,
                    provenance.checksum,
                )
                if value
            ),
        )

    @staticmethod
    def _validate_execution_context(call: ToolCall, context: ExecutionContext) -> None:
        if (
            not context.authenticated
            or context.task_id != call.task_id
            or context.step_id != call.step_id
            or context.user_id != call.user_id
            or context.tenant_id != call.tenant_id
            or context.deadline_at != call.deadline_at
            or context.approval_id != call.approval_id
            or not context.trace_id
            or not context.purpose
        ):
            raise ToolAuthorizationError(
                "Execution context does not match the exact invocation",
                error_code="EXECUTION_CONTEXT_INVALID",
            )

    def _append_audit(self, record: ToolAuditRecord) -> None:
        try:
            self._audit_sink.append(record)
        except Exception as exc:
            raise ToolAuditError() from exc

    def _guard_tool_output(
        self,
        output: JsonObject,
        *,
        tool_name: str,
        source_id: str,
    ) -> tuple[JsonObject | None, tuple[str, ...]]:
        values = dict(output.root)
        finding_codes: list[str] = []
        if tool_name == "knowledge_search":
            raw_matches = values.get("matches")
            if isinstance(raw_matches, list):
                matches: list[JsonValue] = []
                for index, raw in enumerate(raw_matches):
                    if not isinstance(raw, dict):
                        matches.append(raw)
                        continue
                    match = dict(raw)
                    excerpt = match.get("excerpt")
                    if isinstance(excerpt, str):
                        scan = self._injection_detector.scan(
                            excerpt,
                            source_type=ContentSourceType.RETRIEVED_DOCUMENT,
                            source_id=f"{source_id}:match:{index}",
                        )
                        match["excerpt"] = scan.content
                        match["checksum"] = hashlib.sha256(scan.content.encode("utf-8")).hexdigest()
                        finding_codes.extend(finding.category for finding in scan.findings)
                    matches.append(match)
                values["matches"] = matches
        scan_values = dict(values)
        if tool_name == "report_generator":
            scan_values.pop("location", None)
        guard = self._output_guard.guard(
            cast(JsonValue, scan_values),
            source_type=ContentSourceType.TOOL_OUTPUT,
            source_id=source_id,
            target="tool_output",
        )
        finding_codes.extend(finding.category for finding in guard.findings)
        if guard.disposition is OutputDisposition.BLOCKED or guard.content is None:
            return None, tuple(dict.fromkeys(finding_codes))
        if not isinstance(guard.content, dict):
            return None, tuple(dict.fromkeys((*finding_codes, "UNSAFE_TOOL_OUTPUT")))
        safe_values = dict(guard.content)
        if tool_name == "report_generator" and "location" in values:
            safe_values["location"] = values["location"]
        if guard.redactions:
            finding_codes.append("OUTPUT_REDACTED")
        return JsonObject(safe_values), tuple(dict.fromkeys(finding_codes))

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


def _observability_status(
    status: ToolResultStatus,
) -> tuple[str, SpanStatus, str]:
    if status is ToolResultStatus.SUCCESS:
        return "SUCCEEDED", SpanStatus.SUCCEEDED, EventName.TOOL_COMPLETED
    if status is ToolResultStatus.TIMEOUT:
        return "TIMED_OUT", SpanStatus.TIMED_OUT, EventName.TOOL_TIMEOUT
    return "FAILED", SpanStatus.FAILED, EventName.TOOL_FAILED


def _latency_ms(started_at: datetime, completed_at: datetime) -> int:
    return round((completed_at - started_at).total_seconds() * 1000)


def _arguments_hash(arguments: JsonObject) -> str:
    return hashlib.sha256(arguments.model_dump_json().encode("utf-8")).hexdigest()
