"""Map approved external MCP tools into the existing governed ToolRegistry."""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

from copilot.contracts import (
    EvidenceContent,
    EvidenceSourceReference,
    EvidenceType,
    JsonObject,
    MCPInvocationResult,
    MCPInvocationStatus,
    MCPToolCapability,
    RiskLevel,
    ToolApprovalPolicy,
    ToolDefinition,
    ToolIdempotency,
    ToolTimeout,
)
from copilot.tools.base import EvidenceDraft, ToolExecutionContext, ToolExecutionOutput
from copilot.tools.exceptions import (
    ToolBusinessError,
    ToolCancellationError,
    ToolExecutionError,
    ToolPermissionError,
    ToolTimeoutError,
)
from copilot.tools.registry import (
    RegisteredTool,
    RegistrationSource,
    ToolCancellationMode,
    ToolOrigin,
    ToolProvenance,
    ToolRegistrationRequest,
    ToolRegistry,
)


class MCPInvocationSession(Protocol):
    def invoke(
        self,
        capability: MCPToolCapability,
        arguments: JsonObject,
        context: ToolExecutionContext,
    ) -> MCPInvocationResult: ...


class ImportedMCPTool:
    """Tool-compatible adapter; execution still occurs only through ToolExecutor."""

    def __init__(self, capability: MCPToolCapability, session: MCPInvocationSession) -> None:
        self._capability = capability
        self._session = session
        self.definition = ToolDefinition(
            tool_name=capability.name,
            tool_version=(
                f"mcp-{capability.provenance.protocol_revision.value}-"
                f"{capability.provenance.schema_digest[:12]}"
            ),
            description=("Untrusted external MCP capability metadata: " + capability.description)[
                :2048
            ],
            input_schema=capability.input_schema,
            output_schema=capability.output_schema,
            risk_level=RiskLevel.MEDIUM if capability.destructive else RiskLevel.LOW,
            timeout=ToolTimeout(attempt_seconds=60, overall_seconds=60),
            approval_policy=ToolApprovalPolicy(
                policy_id=f"mcp-{capability.origin.server_id}-{capability.name}",
                trigger_conditions=("external_destructive_capability",)
                if capability.destructive
                else (),
                approver_role="quality_data_approver" if capability.destructive else None,
            ),
            idempotency=ToolIdempotency(
                idempotent=capability.idempotent,
                key_components=("normalized_input", "tool_version"),
                reuse_window_seconds=0,
                side_effects=(
                    "External server declares destructive effects"
                    if capability.destructive
                    else "External server effects are untrusted and policy-bounded"
                ),
            ),
        )

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        result = self._session.invoke(self._capability, arguments, context)
        if result.status is MCPInvocationStatus.TIMEOUT:
            raise ToolTimeoutError(error_code="MCP_TIMEOUT", message="MCP invocation timed out")
        if result.status is MCPInvocationStatus.CANCELLED:
            raise ToolCancellationError(message="MCP invocation was cancelled")
        if result.status is MCPInvocationStatus.PERMISSION_DENIED:
            raise ToolPermissionError(
                error_code=result.error.error_code if result.error else "MCP_SCOPE_DENIED",
                message="MCP invocation was denied",
            )
        if result.status is MCPInvocationStatus.BUSINESS_FAILURE:
            raise ToolBusinessError(
                error_code=result.error.error_code if result.error else "MCP_REMOTE_TOOL_ERROR",
                message="External MCP tool reported a business failure",
            )
        if result.status is not MCPInvocationStatus.SUCCESS or result.output is None:
            raise ToolExecutionError(
                error_code=result.error.error_code if result.error else "MCP_INVOCATION_ERROR",
                message="External MCP tool invocation failed",
                recoverable=result.error.recoverable if result.error else False,
            )
        output_bytes = json.dumps(result.output.root, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        evidence = EvidenceDraft(
            source_type=EvidenceType.DOCUMENT,
            source_reference=EvidenceSourceReference(
                reference=JsonObject(
                    {
                        "server_id": self._capability.origin.server_id,
                        "session_id": context.metadata.root.get("mcp_session_id", "bound-session"),
                        "namespace": self._capability.namespace,
                        "capability": self._capability.name,
                        "origin": self._capability.origin.model_dump(mode="json"),
                        "provenance": self._capability.provenance.model_dump(mode="json"),
                        "protocol_revision": self._capability.provenance.protocol_revision.value,
                        "transport": self._capability.origin.transport.value,
                        "invocation_outcome": result.status.value,
                    }
                )
            ),
            content=EvidenceContent(
                data=result.output,
                classification="enterprise-external-untrusted",
                checksum=hashlib.sha256(output_bytes).hexdigest(),
            ),
        )
        return ToolExecutionOutput(result.output, evidence=(evidence,))


class MCPCapabilityImporter:
    """Atomically refresh one server namespace in the existing registry."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def refresh(
        self,
        *,
        namespace: str,
        capabilities: tuple[MCPToolCapability, ...],
        session: MCPInvocationSession,
        expected_generation: int | None = None,
    ) -> tuple[RegisteredTool, ...]:
        requests = tuple(
            ToolRegistrationRequest(
                tool=ImportedMCPTool(capability, session),
                namespace=namespace,
                origin=ToolOrigin(
                    source_id=capability.origin.server_id,
                    origin_type="mcp_server",
                ),
                provenance=ToolProvenance(
                    provider=f"mcp:{capability.origin.connection_id}",
                    revision=capability.provenance.protocol_revision.value,
                    checksum=capability.provenance.schema_digest,
                ),
                schema_version="mcp-tool-capability.v1",
                registration_source=RegistrationSource.DISCOVERY,
                cancellation_mode=ToolCancellationMode.COOPERATIVE,
            )
            for capability in capabilities
        )
        return self._registry.refresh_namespace(
            namespace,
            requests,
            expected_generation=expected_generation,
        )


__all__ = ["ImportedMCPTool", "MCPCapabilityImporter", "MCPInvocationSession"]
