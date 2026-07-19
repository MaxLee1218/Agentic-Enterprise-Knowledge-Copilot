"""Scope-bound human approval contracts."""

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from copilot.contracts.base import ImmutableContractModel
from copilot.contracts.enums import ApprovalStatus
from copilot.contracts.validators import validate_identifier, validate_utc_datetime


class ApprovalRequest(ImmutableContractModel):
    """Immutable approval request and decision bound to one plan action and scope."""

    approval_id: str = Field(description="Globally unique approval identifier")
    task_id: str = Field(description="Task requesting approval")
    planning_version: int = Field(description="Plan version covered by the decision", ge=1)
    action_fingerprint: str = Field(description="Canonical fingerprint of the controlled action")
    controlled_scope: tuple[str, ...] = Field(
        description="Exact data and action scope covered by approval", min_length=1
    )
    reason: str = Field(description="Policy and business reason for approval", min_length=1)
    requester: str = Field(description="Authenticated requesting subject", min_length=1)
    approver: str | None = Field(default=None, description="Authenticated deciding subject")
    required_role: str = Field(description="Role required to make the decision", min_length=1)
    status: ApprovalStatus = Field(description="Current immutable approval decision")
    policy_version: str = Field(description="Approval policy version used", min_length=1)
    created_at: datetime = Field(description="UTC time the request was created")
    decided_at: datetime | None = Field(default=None, description="UTC decision time")
    expires_at: datetime = Field(description="UTC time after which approval cannot be used")

    _validate_ids = field_validator(
        "approval_id", "task_id", "action_fingerprint", "requester", "required_role"
    )(validate_identifier)
    _validate_times = field_validator("created_at", "decided_at", "expires_at")(
        lambda value: validate_utc_datetime(value) if value is not None else value
    )

    @model_validator(mode="after")
    def validate_decision(self) -> "ApprovalRequest":
        """Require coherent timing and actor fields for pending and decided approvals."""
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        if self.status is ApprovalStatus.PENDING:
            if self.approver is not None or self.decided_at is not None:
                raise ValueError("pending approval cannot contain decision fields")
        elif self.approver is None or self.decided_at is None:
            raise ValueError("decided approval must include approver and decided_at")
        return self
