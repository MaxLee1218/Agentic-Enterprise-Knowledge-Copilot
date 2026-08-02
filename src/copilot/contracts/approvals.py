"""Scope-bound Human-in-the-loop approval contracts for the frozen v1.1 design."""

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from copilot.contracts.base import ImmutableContractModel, JsonObject
from copilot.contracts.enums import ApprovalResolutionAction, ApprovalStatus
from copilot.contracts.validators import validate_identifier, validate_utc_datetime


class ApprovalRequest(ImmutableContractModel):
    """Immutable pending or resolved approval version bound to one exact tool action."""

    approval_id: str = Field(description="Globally unique approval identifier")
    task_id: str = Field(description="Task requesting approval")
    tenant_id: str = Field(description="Trusted tenant binding for the controlled action")
    step_id: str = Field(description="Not-yet-executed plan step covered by approval")
    planning_version: int = Field(description="Plan version covered by the decision", ge=1)
    tool_name: str = Field(description="Registered target tool name")
    tool_version: str = Field(description="Registered target tool version")
    input_schema_fingerprint: str = Field(description="Bound registered input-schema digest")
    original_action_fingerprint: str = Field(description="Digest of the proposed action")
    resolved_action_fingerprint: str | None = Field(
        default=None, description="Digest of the approved final action"
    )
    controlled_scope: tuple[str, ...] = Field(
        description="Exact data and action scope covered by approval", min_length=1
    )
    editable_fields: tuple[str, ...] = Field(
        default_factory=tuple, description="Frozen top-level fields eligible for bounded edit"
    )
    proposed_arguments: JsonObject = Field(description="Complete original tool input")
    resolved_arguments: JsonObject | None = Field(
        default=None, description="Complete approved final tool input"
    )
    reason: str = Field(description="Policy and business reason for approval", min_length=1)
    requester: str = Field(description="Authenticated requesting subject", min_length=1)
    approver: str | None = Field(default=None, description="Authenticated deciding subject")
    required_role: str = Field(description="Role required to make the decision", min_length=1)
    status: ApprovalStatus = Field(description="Current immutable approval status")
    resolution_action: ApprovalResolutionAction | None = Field(
        default=None, description="One-time human resolution action"
    )
    resolution_reason: str | None = Field(
        default=None, description="Human edit suggestion or rejection reason"
    )
    policy_version: str = Field(description="Approval policy version used", min_length=1)
    created_at: datetime = Field(description="UTC time the request was created")
    decided_at: datetime | None = Field(default=None, description="UTC decision time")
    expires_at: datetime = Field(description="UTC time after which approval cannot be used")
    version: int = Field(default=1, description="Optimistic concurrency version", ge=1)

    _validate_ids = field_validator(
        "approval_id",
        "task_id",
        "tenant_id",
        "step_id",
        "tool_name",
        "tool_version",
        "input_schema_fingerprint",
        "original_action_fingerprint",
        "resolved_action_fingerprint",
        "requester",
        "required_role",
    )(lambda value: validate_identifier(value) if value is not None else value)
    _validate_times = field_validator("created_at", "decided_at", "expires_at")(
        lambda value: validate_utc_datetime(value) if value is not None else value
    )

    @model_validator(mode="after")
    def validate_decision(self) -> "ApprovalRequest":
        """Require status, action, actor, arguments, fingerprints, and timing to agree."""
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        if len(set(self.editable_fields)) != len(self.editable_fields):
            raise ValueError("editable_fields must not contain duplicates")
        if self.status is ApprovalStatus.PENDING:
            if any(
                value is not None
                for value in (
                    self.approver,
                    self.decided_at,
                    self.resolution_action,
                    self.resolution_reason,
                    self.resolved_arguments,
                    self.resolved_action_fingerprint,
                )
            ):
                raise ValueError("pending approval cannot contain resolution fields")
            return self
        if self.status is ApprovalStatus.APPROVED:
            if self.approver is None or self.decided_at is None:
                raise ValueError("approved approval must include approver and decided_at")
            if self.resolution_action not in {
                ApprovalResolutionAction.APPROVE,
                ApprovalResolutionAction.EDIT,
            }:
                raise ValueError("approved approval requires APPROVE or EDIT action")
            if self.resolved_arguments is None or self.resolved_action_fingerprint is None:
                raise ValueError("approved approval requires resolved arguments and fingerprint")
            if self.resolution_action is ApprovalResolutionAction.APPROVE:
                if self.resolved_arguments != self.proposed_arguments:
                    raise ValueError("APPROVE must preserve proposed arguments")
                if self.resolved_action_fingerprint != self.original_action_fingerprint:
                    raise ValueError("APPROVE must preserve the original action fingerprint")
            elif not self.resolution_reason or not self.resolution_reason.strip():
                raise ValueError("EDIT requires a resolution reason")
            return self
        if self.status is ApprovalStatus.REJECTED:
            if self.approver is None or self.decided_at is None:
                raise ValueError("rejected approval must include approver and decided_at")
            if self.resolution_action is not ApprovalResolutionAction.REJECT:
                raise ValueError("rejected approval requires REJECT action")
            if not self.resolution_reason or not self.resolution_reason.strip():
                raise ValueError("REJECT requires a resolution reason")
            if self.resolved_arguments is not None or self.resolved_action_fingerprint is not None:
                raise ValueError("rejected approval cannot contain executable arguments")
            return self
        if self.resolution_action is not None:
            raise ValueError("expired or revoked approval cannot contain a resolution action")
        if self.resolved_arguments is not None or self.resolved_action_fingerprint is not None:
            raise ValueError("expired or revoked approval cannot contain executable arguments")
        return self

    @property
    def action_fingerprint(self) -> str:
        """Return the effective digest for compatibility with read-only verification callers."""
        return self.resolved_action_fingerprint or self.original_action_fingerprint
