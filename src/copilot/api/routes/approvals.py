"""Thin HTTP adapter for resolving one persisted approval."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from copilot.api.dependencies import get_approval_service, get_caller_context
from copilot.api.schemas.approvals import (
    ApprovalDetailResponse,
    ApprovalResolutionRequest,
    ApprovalResolutionResponse,
)
from copilot.api.schemas.tasks import TaskErrorResponse
from copilot.contracts import ApprovalResolutionAction, JsonObject
from copilot.services.approval_service import ApprovalResolutionCommand, ApprovalService
from copilot.services.task_intake import TrustedCallerContext

router = APIRouter(prefix="/v1/tasks", tags=["approvals"])


@router.get(
    "/{task_id}/approvals/{approval_id}",
    response_model=ApprovalDetailResponse,
    status_code=200,
    responses={
        403: {"model": TaskErrorResponse},
        404: {"model": TaskErrorResponse},
        409: {"model": TaskErrorResponse},
        422: {"model": TaskErrorResponse},
        500: {"model": TaskErrorResponse},
    },
)
def get_approval(
    task_id: str,
    approval_id: str,
    request: Request,
    service: Annotated[ApprovalService, Depends(get_approval_service)],
    caller: Annotated[TrustedCallerContext, Depends(get_caller_context)],
) -> ApprovalDetailResponse:
    """Return the complete proposed input only after tenant and approver-role checks."""
    approval = service.get(
        task_id,
        approval_id,
        caller,
        trace_id=str(request.state.trace_id),
    )
    return ApprovalDetailResponse(
        approval_id=approval.approval_id,
        task_id=approval.task_id,
        status=approval.status.value,
        step_id=approval.step_id,
        planning_version=approval.planning_version,
        tool_name=approval.tool_name,
        tool_version=approval.tool_version,
        editable_fields=approval.editable_fields,
        proposed_arguments=dict(approval.proposed_arguments.root),
        resolved_arguments=(
            dict(approval.resolved_arguments.root)
            if approval.resolved_arguments is not None
            else None
        ),
        reason=approval.reason,
        resolution_action=(
            approval.resolution_action.value if approval.resolution_action is not None else None
        ),
        resolution_reason=approval.resolution_reason,
        created_at=approval.created_at,
        expires_at=approval.expires_at,
        resolved_at=approval.decided_at,
        resolved_by=approval.approver,
    )


@router.post(
    "/{task_id}/approvals/{approval_id}",
    response_model=ApprovalResolutionResponse,
    status_code=200,
    responses={
        400: {"model": TaskErrorResponse},
        403: {"model": TaskErrorResponse},
        404: {"model": TaskErrorResponse},
        409: {"model": TaskErrorResponse},
        422: {"model": TaskErrorResponse},
        500: {"model": TaskErrorResponse},
    },
)
def resolve_approval(
    task_id: str,
    approval_id: str,
    resolution: ApprovalResolutionRequest,
    request: Request,
    service: Annotated[ApprovalService, Depends(get_approval_service)],
    caller: Annotated[TrustedCallerContext, Depends(get_caller_context)],
) -> ApprovalResolutionResponse:
    """Resolve and resume through ApprovalService; the route never calls a tool directly."""
    result = service.resolve(
        ApprovalResolutionCommand(
            task_id=task_id,
            approval_id=approval_id,
            action=ApprovalResolutionAction(resolution.action.value.upper()),
            reason=resolution.reason,
            edited_arguments=(
                JsonObject(resolution.edited_arguments)
                if resolution.edited_arguments is not None
                else None
            ),
        ),
        caller,
        trace_id=str(request.state.trace_id),
    )
    approval = result.approval
    assert approval.resolution_action is not None
    assert approval.decided_at is not None
    assert approval.approver is not None
    return ApprovalResolutionResponse(
        approval_id=approval.approval_id,
        approval_status=approval.status.value,
        resolution_action=approval.resolution_action.value,
        task_id=approval.task_id,
        task_status=result.task_status.value,
        resolved_at=approval.decided_at,
        resolved_by=approval.approver,
        resume_status=result.task_status.value,
        trace_id=result.trace_id or str(request.state.trace_id),
    )


__all__ = ["get_approval", "resolve_approval", "router"]
