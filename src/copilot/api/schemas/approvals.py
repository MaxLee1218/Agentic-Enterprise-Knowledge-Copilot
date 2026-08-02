"""HTTP-only schemas for resolving v1.1 Human-in-the-loop approvals."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class ApprovalAction(StrEnum):
    """Public lowercase approval actions."""

    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


class ApprovalResolutionRequest(BaseModel):
    """One strictly validated approval resolution request body."""

    model_config = ConfigDict(extra="forbid")

    action: ApprovalAction
    edited_arguments: dict[str, JsonValue] | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_action_payload(self) -> ApprovalResolutionRequest:
        """Reject ambiguous combinations before entering the application service."""
        if self.action is ApprovalAction.EDIT:
            if self.edited_arguments is None:
                raise ValueError("edit requires edited_arguments")
            if self.reason is None or not self.reason.strip():
                raise ValueError("edit requires a reason")
        elif self.edited_arguments is not None:
            raise ValueError("edited_arguments is only valid for edit")
        if self.action is ApprovalAction.REJECT and (
            self.reason is None or not self.reason.strip()
        ):
            raise ValueError("reject requires a reason")
        return self


class ApprovalResolutionResponse(BaseModel):
    """Stable decision and checkpoint-resume response."""

    model_config = ConfigDict(extra="forbid")

    approval_id: str
    approval_status: str
    resolution_action: str
    task_id: str
    task_status: str
    resolved_at: datetime
    resolved_by: str
    resume_status: str
    trace_id: str


class ApprovalDetailResponse(BaseModel):
    """Authorized approval detail needed to render a human decision form."""

    model_config = ConfigDict(extra="forbid")

    approval_id: str
    task_id: str
    status: str
    step_id: str
    planning_version: int
    tool_name: str
    tool_version: str
    editable_fields: tuple[str, ...]
    proposed_arguments: dict[str, JsonValue]
    resolved_arguments: dict[str, JsonValue] | None
    reason: str
    resolution_action: str | None
    resolution_reason: str | None
    created_at: datetime
    expires_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None


__all__ = [
    "ApprovalAction",
    "ApprovalDetailResponse",
    "ApprovalResolutionRequest",
    "ApprovalResolutionResponse",
]
