"""Explicit public Artifact metadata schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ArtifactMetadataResponse(BaseModel):
    """Safe immutable Artifact metadata without a storage location."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    task_id: str
    format: Literal["PDF", "JSON"]
    filename: str
    media_type: str
    checksum: str
    size_bytes: int
    created_at: datetime


class ArtifactListResponse(BaseModel):
    """Deterministically ordered task Artifact collection."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    artifacts: tuple[ArtifactMetadataResponse, ...]


__all__ = ["ArtifactListResponse", "ArtifactMetadataResponse"]
