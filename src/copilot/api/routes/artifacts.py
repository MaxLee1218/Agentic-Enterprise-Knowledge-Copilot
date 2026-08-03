"""Authorized Artifact listing and streaming routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from copilot.api.dependencies import (
    get_artifact_service,
    get_caller_context,
)
from copilot.api.mappers import artifact_metadata_response
from copilot.api.schemas.artifacts import ArtifactListResponse
from copilot.api.schemas.tasks import TaskErrorResponse
from copilot.services.artifact_service import ArtifactService
from copilot.services.task_intake import TrustedCallerContext

router = APIRouter(prefix="/v1/tasks", tags=["artifacts"])


@router.get(
    "/{task_id}/artifacts",
    response_model=ArtifactListResponse,
    operation_id="list_task_artifacts",
    responses={
        403: {"model": TaskErrorResponse},
        404: {"model": TaskErrorResponse},
        422: {"model": TaskErrorResponse},
        500: {"model": TaskErrorResponse},
    },
)
def list_task_artifacts(
    task_id: str,
    request: Request,
    service: Annotated[ArtifactService, Depends(get_artifact_service)],
    caller: Annotated[TrustedCallerContext, Depends(get_caller_context)],
) -> ArtifactListResponse:
    """List authorized Artifact metadata without governed storage locations."""
    views = service.list_task_artifacts(
        task_id,
        caller,
        trace_id=str(request.state.trace_id),
    )
    return ArtifactListResponse(
        task_id=task_id,
        artifacts=tuple(artifact_metadata_response(view) for view in views),
    )


@router.get(
    "/{task_id}/artifacts/{artifact_id}",
    response_class=FileResponse,
    operation_id="download_task_artifact",
    responses={
        200: {
            "description": "Artifact byte stream",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        },
        403: {"model": TaskErrorResponse},
        404: {"model": TaskErrorResponse},
        410: {"model": TaskErrorResponse},
        422: {"model": TaskErrorResponse},
        500: {"model": TaskErrorResponse},
    },
)
def download_task_artifact(
    task_id: str,
    artifact_id: str,
    request: Request,
    service: Annotated[ArtifactService, Depends(get_artifact_service)],
    caller: Annotated[TrustedCallerContext, Depends(get_caller_context)],
) -> FileResponse:
    """Stream one repository-controlled Artifact after ownership validation."""
    download = service.get_task_artifact(
        task_id,
        artifact_id,
        caller,
        trace_id=str(request.state.trace_id),
    )
    return FileResponse(
        path=download.path,
        media_type=download.media_type,
        filename=download.filename,
        headers={
            "ETag": f'"{download.checksum}"',
            "X-Artifact-ID": download.artifact_id,
        },
    )


__all__ = ["download_task_artifact", "list_task_artifacts", "router"]
