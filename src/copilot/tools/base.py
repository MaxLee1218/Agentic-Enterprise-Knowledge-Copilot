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


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """Trusted execution context supplied by the runtime, never by model output."""

    call: ToolCall
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
    step_id: str
    tool_name: str
    tool_version: str
    status: ToolResultStatus
    latency_ms: int
    timestamp: datetime
    error_code: str | None = None


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
    """Pre-execution policy and approval boundary."""

    def authorize(self, call: ToolCall, definition: ToolDefinition) -> None:
        """Return only when policy and any required approval cover the exact call."""
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
