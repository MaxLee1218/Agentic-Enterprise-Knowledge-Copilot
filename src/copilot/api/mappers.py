"""Central mappings from application views to stable HTTP response schemas."""

from copilot.api.schemas.artifacts import ArtifactMetadataResponse
from copilot.api.schemas.tasks import (
    ApprovalSummaryResponse,
    ClarificationRoundResponse,
    InitialUserMessageResponse,
    PendingClarificationQuestionResponse,
    PendingClarificationResponse,
    PublicStepStatus,
    TaskDetailResponse,
    TaskEvidenceResponse,
    TaskInteractionProjectionResponse,
    TaskListItemResponse,
    TaskPhaseEventResponse,
    TaskResponse,
    TaskResultSummaryResponse,
    TaskStepResponse,
)
from copilot.contracts import (
    ApprovalResolutionAction,
    ApprovalStatus,
    ClarificationQuestion,
    ClarificationStatus,
    EvidenceType,
    RuntimeStatus,
    TaskStatus,
    TaskType,
)
from copilot.services.task_views import (
    TaskArtifactView,
    TaskDetailView,
    TaskEvidenceView,
    TaskListItemView,
    TaskStepView,
    TaskSummaryView,
)


def task_response(view: TaskSummaryView) -> TaskResponse:
    """Map one application task summary to its public HTTP DTO."""
    return TaskResponse(
        task_id=view.task_id,
        trace_id=view.trace_id,
        status=TaskStatus(view.status),
        runtime_status=RuntimeStatus(view.runtime_status),
        task_type=TaskType(view.task_type) if view.task_type is not None else None,
        created_at=view.created_at,
        started_at=view.started_at,
        completed_at=view.completed_at,
        cancelled_at=view.cancelled_at,
        current_step=view.current_step,
        task_summary=view.task_summary,
        pending_approval_id=view.pending_approval_id,
        pending_clarification=(
            PendingClarificationResponse(
                clarification_id=view.pending_clarification.clarification_id,
                round=view.pending_clarification.round,
                questions=tuple(
                    PendingClarificationQuestionResponse(
                        field=question.field,
                        reason=question.reason,
                        prompt=question.prompt,
                        input_type=question.input_type,
                        required=question.required,
                        allowed_values=question.allowed_values,
                        constraints=dict(question.constraints.root),
                    )
                    for question in view.pending_clarification.questions
                ),
                created_at=view.pending_clarification.created_at,
            )
            if view.pending_clarification is not None
            else None
        ),
        step_count=view.step_count,
        evidence_count=view.evidence_count,
        artifact_count=view.artifact_count,
        error_summary=view.error_summary,
    )


def task_list_item_response(view: TaskListItemView) -> TaskListItemResponse:
    """Map one lightweight task sidebar row."""
    return TaskListItemResponse(
        task_id=view.task_id,
        task_summary=view.task_summary,
        status=TaskStatus(view.status),
        runtime_status=RuntimeStatus(view.runtime_status),
        task_type=TaskType(view.task_type) if view.task_type is not None else None,
        created_at=view.created_at,
    )


def task_detail_response(view: TaskDetailView) -> TaskDetailResponse:
    """Map existing task fields and the Task-scoped interaction projection."""
    summary = task_response(view)
    projection = view.interaction_projection
    if projection is None:
        raise ValueError("TaskDetailView requires an interaction projection")
    return TaskDetailResponse(
        **summary.model_dump(),
        interaction_projection=TaskInteractionProjectionResponse(
            schema_version=projection.schema_version,
            initial_user_message=InitialUserMessageResponse(
                display_text=projection.initial_user_message.display_text,
                created_at=projection.initial_user_message.created_at,
            ),
            clarification_rounds=tuple(
                ClarificationRoundResponse(
                    clarification_id=item.clarification_id,
                    round=item.round,
                    status=ClarificationStatus(item.status),
                    questions=tuple(
                        _clarification_question(question) for question in item.questions
                    ),
                    response_display_text=item.response_display_text,
                    created_at=item.created_at,
                    submitted_at=item.submitted_at,
                    resolved_at=item.resolved_at,
                )
                for item in projection.clarification_rounds
            ),
            phase_events=tuple(
                TaskPhaseEventResponse(
                    phase=TaskStatus(item.phase),
                    occurred_at=item.occurred_at,
                )
                for item in projection.phase_events
            ),
            approval_summaries=tuple(
                ApprovalSummaryResponse(
                    approval_id=item.approval_id,
                    status=ApprovalStatus(item.status),
                    safe_label=item.safe_label,
                    resolution_action=(
                        ApprovalResolutionAction(item.resolution_action)
                        if item.resolution_action is not None
                        else None
                    ),
                    created_at=item.created_at,
                    resolved_at=item.resolved_at,
                )
                for item in projection.approval_summaries
            ),
            result=(
                TaskResultSummaryResponse(
                    final_status=TaskStatus(projection.result.final_status),
                    safe_summary=projection.result.safe_summary,
                )
                if projection.result is not None
                else None
            ),
        ),
    )


def _clarification_question(
    question: ClarificationQuestion,
) -> PendingClarificationQuestionResponse:
    """Map one typed clarification question without widening its data."""
    return PendingClarificationQuestionResponse(
        field=question.field,
        reason=question.reason,
        prompt=question.prompt,
        input_type=question.input_type,
        required=question.required,
        allowed_values=question.allowed_values,
        constraints=dict(question.constraints.root),
    )


def task_step_response(view: TaskStepView) -> TaskStepResponse:
    """Map one safe step view to its HTTP DTO."""
    return TaskStepResponse(
        step_id=view.step_id,
        tool_name=view.tool_name,
        purpose=view.purpose,
        status=PublicStepStatus(view.status),
        depends_on=view.depends_on,
        attempt_count=view.attempt_count,
        retry_count=view.retry_count,
        started_at=view.started_at,
        completed_at=view.completed_at,
        latency_ms=view.latency_ms,
        evidence_ids=view.evidence_ids,
        error_code=view.error_code,
        error_message=view.error_message,
    )


def task_evidence_response(view: TaskEvidenceView) -> TaskEvidenceResponse:
    """Map one minimized Evidence view to its HTTP DTO."""
    return TaskEvidenceResponse(
        evidence_id=view.evidence_id,
        type=EvidenceType(view.type),
        source=view.source,
        produced_by=view.produced_by,
        step_id=view.step_id,
        lineage=view.lineage,
        confidence=view.confidence,
        created_at=view.created_at,
        query_id=view.query_id,
        document_source=view.document_source,
        formula=view.formula,
        input_evidence_ids=view.input_evidence_ids,
        content_summary=view.content_summary,
    )


def artifact_metadata_response(view: TaskArtifactView) -> ArtifactMetadataResponse:
    """Map safe Artifact metadata without a storage path."""
    return ArtifactMetadataResponse(
        artifact_id=view.artifact_id,
        task_id=view.task_id,
        format="PDF" if view.format == "PDF" else "JSON",
        filename=view.filename,
        media_type=view.media_type,
        checksum=view.checksum,
        size_bytes=view.size_bytes,
        created_at=view.created_at,
    )


__all__ = [
    "artifact_metadata_response",
    "task_detail_response",
    "task_evidence_response",
    "task_list_item_response",
    "task_response",
    "task_step_response",
]
