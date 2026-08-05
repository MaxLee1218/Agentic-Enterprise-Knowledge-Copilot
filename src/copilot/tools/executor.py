"""Governed lifecycle executor for registered enterprise tool plugins."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from copilot.contracts import (
    ErrorType,
    JsonObject,
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
from copilot.tools.base import (
    ContextualToolAuthorizer,
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

if TYPE_CHECKING:
    from copilot.services.task_intake import TrustedTaskContext


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
        self._output_guard = output_guard or OutputGuard()
        self._injection_detector = injection_detector or PromptInjectionDetector()

    def execute(
        self,
        call: ToolCall,
        *,
        attempt: int = 1,
        security_context: TrustedTaskContext | None = None,
    ) -> ToolResult:
        """Run one governed attempt through validation, policy, evidence, and audit."""
        if not 1 <= attempt <= 3:
            raise ValueError("attempt must be between 1 and 3")
        started_at = self._clock()
        try:
            tool = self._registry.get(call.tool_name)
        except ToolRuntimeError:
            self._append_audit(
                ToolAuditRecord(
                    tool_call_id=call.tool_call_id,
                    task_id=call.task_id,
                    step_id=call.step_id,
                    tool_name=call.tool_name,
                    tool_version=call.tool_version,
                    status=ToolResultStatus.PERMISSION_DENIED,
                    latency_ms=0,
                    timestamp=started_at,
                    attempt=attempt,
                    error_code="TOOL_NOT_ALLOWED",
                    principal_id=call.user_id,
                    policy_decision="DENY",
                    reason_code="TOOL_NOT_ALLOWED",
                )
            )
            raise
        self._validate_call(call, tool.definition, started_at, attempt)

        try:
            if security_context is not None and hasattr(self._authorizer, "authorize_with_context"):
                contextual = cast(ContextualToolAuthorizer, self._authorizer)
                contextual.authorize_with_context(call, tool.definition, security_context)
            else:
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
                ToolExecutionContext(
                    call=call,
                    metadata=JsonObject(
                        {
                            "attempt": attempt,
                            "trace_id": (
                                security_context.trace_id
                                if security_context is not None
                                else call.tool_call_id
                            ),
                            "roles": (
                                list(security_context.roles)
                                if security_context is not None
                                else ["quality_analyst"]
                            ),
                            "is_demo_identity": (
                                security_context.is_demo_identity
                                if security_context is not None
                                else True
                            ),
                            "purpose": (
                                security_context.purpose
                                if security_context is not None
                                else "supplier_quality_analysis.v1"
                            ),
                        }
                    ),
                ),
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
        self._audit(result, principal_id=call.user_id, security_finding_codes=finding_codes)
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
                    principal_id=call.user_id,
                    policy_decision="DENY",
                    reason_code=exc.error.error_code,
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
            principal_id=call.user_id,
            security_finding_codes=security_finding_codes,
        )
        return result

    def _audit(
        self,
        result: ToolResult,
        *,
        principal_id: str,
        security_finding_codes: tuple[str, ...] = (),
    ) -> None:
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
                principal_id=principal_id,
                policy_decision=("ALLOW" if result.status is ToolResultStatus.SUCCESS else "DENY"),
                reason_code=(result.error.error_code if result.error is not None else "ALLOWED"),
                security_finding_codes=security_finding_codes,
            )
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


def _latency_ms(started_at: datetime, completed_at: datetime) -> int:
    return round((completed_at - started_at).total_seconds() * 1000)
