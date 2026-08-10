"""Governed MCP Tool provider routed through the existing Registry and Executor."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from uuid import uuid4

from pydantic import JsonValue

from copilot.contracts import (
    JsonObject,
    MCPCapabilityType,
    MCPClientIdentity,
    MCPErrorDetail,
    MCPInvocationMetadata,
    MCPInvocationResult,
    MCPInvocationStatus,
    MCPToolCapability,
    ToolCall,
    ToolResultStatus,
)
from copilot.contracts.validators import utc_now
from copilot.mcp.errors import MCPAuthorizationError, MCPInvocationError
from copilot.mcp.server.capability_exporter import MCPCapabilityExporter
from copilot.persistence.mcp_session_repository import MCPInvocationRepository
from copilot.services.execution import ExecutionContext
from copilot.tools.cancellation import CancellationToken
from copilot.tools.executor import ToolExecutor
from copilot.tools.registry import ToolRegistry


class MCPToolProvider:
    """Translate a protocol request into the exact existing governed invocation envelope."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        executor: ToolExecutor,
        exporter: MCPCapabilityExporter,
        invocation_repository: MCPInvocationRepository,
        require_task_context: bool = True,
    ) -> None:
        self._registry = registry
        self._executor = executor
        self._exporter = exporter
        self._invocations = invocation_repository
        self._require_task_context = require_task_context

    def list_tools(self, identity: MCPClientIdentity) -> tuple[MCPToolCapability, ...]:
        return self._exporter.list(identity)

    def invoke(
        self,
        name: str,
        arguments: JsonObject,
        identity: MCPClientIdentity,
        metadata: JsonObject,
    ) -> MCPInvocationResult:
        capability, rule = self._exporter.require_invocation(name, identity)
        trusted = _trusted_metadata(metadata)
        if self._require_task_context and not trusted:
            raise MCPAuthorizationError("MCP tool invocation requires trusted task metadata")
        _bind_metadata_identity(trusted, identity)
        task_id = _required_text(trusted, "task_id", fallback=f"MCP-{uuid4().hex}")
        trace_id = _required_text(trusted, "trace_id", fallback=f"TRACE-{uuid4().hex}")
        step_id = _required_text(trusted, "step_id", fallback=f"STEP-{uuid4().hex}")
        tool_call_id = _required_text(trusted, "tool_call_id", fallback=f"TC-MCP-{uuid4().hex}")
        approval_id = trusted.get("approval_id")
        if approval_id is not None and not isinstance(approval_id, str):
            raise MCPAuthorizationError("MCP approval identifier is invalid")
        definition = self._registry.get(name).definition
        deadline = utc_now() + timedelta(seconds=definition.timeout.attempt_seconds)
        idempotency_key = hashlib.sha256(
            f"{definition.tool_version}:{arguments.model_dump_json()}".encode()
        ).hexdigest()
        call = ToolCall(
            tool_call_id=tool_call_id,
            task_id=task_id,
            step_id=step_id,
            tool_name=name,
            tool_version=definition.tool_version,
            input=arguments,
            idempotency_key=idempotency_key,
            approval_id=approval_id,
            deadline_at=deadline,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
        )
        execution = ExecutionContext(
            task_id=task_id,
            trace_id=trace_id,
            step_id=step_id,
            user_id=identity.user_id,
            tenant_id=identity.tenant_id,
            roles=identity.roles,
            scopes=identity.scopes,
            data_scope=identity.data_scope,
            purpose=identity.purpose,
            authentication_source=identity.authentication_source,
            is_demo_identity=False,
            authenticated=True,
            deadline_at=deadline,
            approval_required=rule.require_approval,
            approval_id=approval_id,
            cancellation=CancellationToken(),
        )
        started_at = utc_now()
        try:
            result = self._executor.execute(call, execution)
        except Exception as exc:
            raise MCPInvocationError("Governed MCP tool execution failed") from exc
        completed_at = utc_now()
        status = _mcp_status(result.status)
        output = result.output if status is MCPInvocationStatus.SUCCESS else None
        error = (
            None
            if status is MCPInvocationStatus.SUCCESS
            else MCPErrorDetail(
                error_code=result.error.error_code if result.error else "MCP_INVOCATION_ERROR",
                message=result.error.message if result.error else "MCP invocation failed",
                recoverable=result.error.recoverable if result.error else False,
            )
        )
        invocation_id = f"MCPINV-{uuid4().hex}"
        artifact_ids = _artifact_ids(output)
        invocation_result = MCPInvocationResult(
            invocation_id=invocation_id,
            status=status,
            output=output,
            error=error,
            evidence_ids=result.evidence_ids,
            artifact_ids=artifact_ids,
            started_at=started_at,
            completed_at=completed_at,
        )
        session_id = _required_text(trusted, "session_id", fallback=f"MCP-{uuid4().hex}")
        connection_id = _required_text(
            trusted,
            "connection_id",
            fallback=capability.origin.connection_id,
        )
        self._invocations.append(
            MCPInvocationMetadata(
                invocation_id=invocation_id,
                task_id=task_id,
                trace_id=trace_id,
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                client_id=identity.client_id,
                server_id=capability.origin.server_id,
                session_id=session_id,
                protocol_revision=capability.provenance.protocol_revision,
                transport=capability.origin.transport,
                namespace=capability.namespace,
                capability_name=capability.name,
                capability_type=MCPCapabilityType.TOOL,
                policy_decision="ALLOW",
                approval_id=approval_id,
                origin=capability.origin.model_copy(update={"connection_id": connection_id}),
                provenance=capability.provenance,
                latency_ms=invocation_result.latency_ms,
                retry_count=0,
                outcome=status,
                typed_error=error.error_code if error else None,
                evidence_ids=result.evidence_ids,
                artifact_ids=artifact_ids,
                timestamp=completed_at,
            )
        )
        return invocation_result


def _trusted_metadata(metadata: JsonObject) -> dict[str, JsonValue]:
    root = metadata.root
    value = root.get("copilot")
    return dict(value) if isinstance(value, dict) else {}


def _bind_metadata_identity(metadata: dict[str, JsonValue], identity: MCPClientIdentity) -> None:
    for key, expected in (("tenant_id", identity.tenant_id), ("user_id", identity.user_id)):
        supplied = metadata.get(key)
        if supplied is not None and supplied != expected:
            raise MCPAuthorizationError("MCP task metadata identity does not match the token")


def _required_text(metadata: dict[str, JsonValue], key: str, *, fallback: str) -> str:
    value = metadata.get(key, fallback)
    if not isinstance(value, str) or not value:
        raise MCPAuthorizationError("MCP task metadata is invalid")
    return value


def _mcp_status(status: ToolResultStatus) -> MCPInvocationStatus:
    return {
        ToolResultStatus.SUCCESS: MCPInvocationStatus.SUCCESS,
        ToolResultStatus.BUSINESS_FAILURE: MCPInvocationStatus.BUSINESS_FAILURE,
        ToolResultStatus.PERMISSION_DENIED: MCPInvocationStatus.PERMISSION_DENIED,
        ToolResultStatus.TECHNICAL_FAILURE: MCPInvocationStatus.TECHNICAL_FAILURE,
        ToolResultStatus.TIMEOUT: MCPInvocationStatus.TIMEOUT,
    }[status]


def _artifact_ids(output: JsonObject | None) -> tuple[str, ...]:
    if output is None:
        return ()
    value = output.root.get("artifact_id")
    return (value,) if isinstance(value, str) else ()


__all__ = ["MCPToolProvider"]
