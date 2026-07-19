"""Stable public domain-contract API for the enterprise copilot."""

from copilot.contracts.approvals import ApprovalRequest
from copilot.contracts.artifacts import Artifact
from copilot.contracts.base import ContractModel, ImmutableContractModel, JsonObject
from copilot.contracts.enums import (
    ApprovalStatus,
    ArtifactType,
    CapabilityName,
    ErrorType,
    EvidenceType,
    ReportLanguage,
    RiskLevel,
    StepResultStatus,
    StepType,
    TaskStatus,
    TaskType,
    ToolResultStatus,
)
from copilot.contracts.errors import DomainError, TaskError
from copilot.contracts.evidence import EvidenceContent, EvidenceItem, EvidenceSourceReference
from copilot.contracts.plans import RetryPolicy, StepResult, TaskPlan, TaskStep
from copilot.contracts.tasks import (
    ApprovalRequirement,
    ExpectedOutput,
    TaskConstraints,
    TaskContract,
    TaskRequest,
    TaskResult,
    TaskState,
)
from copilot.contracts.tools import (
    ToolApprovalPolicy,
    ToolCall,
    ToolDefinition,
    ToolIdempotency,
    ToolResult,
    ToolTimeout,
)

__all__ = [
    "ApprovalRequest",
    "ApprovalRequirement",
    "ApprovalStatus",
    "Artifact",
    "ArtifactType",
    "CapabilityName",
    "ContractModel",
    "DomainError",
    "ErrorType",
    "EvidenceContent",
    "EvidenceItem",
    "EvidenceSourceReference",
    "EvidenceType",
    "ExpectedOutput",
    "ImmutableContractModel",
    "JsonObject",
    "ReportLanguage",
    "RetryPolicy",
    "RiskLevel",
    "StepResult",
    "StepResultStatus",
    "StepType",
    "TaskConstraints",
    "TaskContract",
    "TaskError",
    "TaskPlan",
    "TaskRequest",
    "TaskResult",
    "TaskState",
    "TaskStatus",
    "TaskStep",
    "TaskType",
    "ToolApprovalPolicy",
    "ToolCall",
    "ToolDefinition",
    "ToolIdempotency",
    "ToolResult",
    "ToolResultStatus",
    "ToolTimeout",
]
