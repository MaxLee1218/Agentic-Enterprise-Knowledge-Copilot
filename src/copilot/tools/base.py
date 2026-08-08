"""Runtime abstractions for governed enterprise tools.

The runtime types in this module are deliberately separate from persisted contracts. Tools
produce a payload and evidence drafts; the executor alone creates the authoritative ToolResult
and delegates immutable evidence registration to the evidence ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from copilot.contracts import (
    EvidenceContent,
    EvidenceItem,
    EvidenceSourceReference,
    EvidenceType,
    JsonObject,
    ToolCall,
    ToolDefinition,
    ToolResultStatus,
)
from copilot.services.execution import ExecutionContext
from copilot.tools.cancellation import CancellationToken


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """Trusted execution context supplied by the runtime, never by model output."""

    call: ToolCall
    trace_id: str = ""
    tenant_id: str = ""
    user_id: str = ""
    roles: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    purpose: str = ""
    cancellation: CancellationToken = field(default_factory=CancellationToken)
    metadata: JsonObject = field(default_factory=lambda: JsonObject({}))


@dataclass(frozen=True, slots=True)
class EvidenceDraft:
    """Evidence content awaiting task, step, call, timestamp, and identifier binding."""

    source_type: EvidenceType
    source_reference: EvidenceSourceReference
    content: EvidenceContent


@dataclass(frozen=True, slots=True)
class ToolExecutionOutput:
    """Schema-validatable output returned by a tool adapter to the executor."""

    output: JsonObject
    evidence: tuple[EvidenceDraft, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolAuditRecord:
    """Minimal, non-sensitive, append-only record of one execution attempt."""

    tool_call_id: str
    task_id: str
    tenant_id: str
    trace_id: str
    step_id: str
    tool_name: str
    tool_version: str
    status: ToolResultStatus
    latency_ms: int
    timestamp: datetime
    attempt: int = 1
    error_code: str | None = None
    principal_id: str | None = None
    scopes: tuple[str, ...] = ()
    purpose: str | None = None
    approval_id: str | None = None
    arguments_hash: str | None = None
    tool_origin: str = "local"
    tool_provenance: str = "built-in"
    policy_decision: str | None = None
    reason_code: str | None = None
    security_finding_codes: tuple[str, ...] = ()


class Tool(Protocol):
    """Plugin interface implemented by every governed tool adapter."""

    definition: ToolDefinition

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        """Execute one already-authorized attempt without orchestration side effects."""
        ...


class ToolRunner(Protocol):
    """Bounded execution mechanism for synchronous tool adapters."""

    def run(
        self,
        tool: Tool,
        arguments: JsonObject,
        context: ToolExecutionContext,
        timeout_seconds: float,
    ) -> ToolExecutionOutput:
        """Run one tool attempt or raise a safe runtime exception."""
        ...


class ToolAuthorizer(Protocol):
    """Mandatory contextual pre-execution policy and approval boundary."""

    def authorize_with_context(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        execution_context: ExecutionContext,
    ) -> None:
        """Return only when current identity, purpose, policy, and scope authorize the call."""
        ...


class EvidenceRecorder(Protocol):
    """Persistence boundary for immutable evidence and lineage."""

    def record(self, call: ToolCall, drafts: tuple[EvidenceDraft, ...]) -> tuple[EvidenceItem, ...]:
        """Bind and persist evidence drafts to the invocation envelope."""
        ...


class ToolAuditSink(Protocol):
    """Append-only persistence boundary for tool execution audit records."""

    def append(self, record: ToolAuditRecord) -> None:
        """Persist one immutable record or fail closed."""
        ...
