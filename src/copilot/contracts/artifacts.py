"""Immutable deliverable artifact metadata contracts."""

from datetime import datetime

from pydantic import Field, field_validator

from copilot.contracts.base import ImmutableContractModel
from copilot.contracts.enums import ArtifactType
from copilot.contracts.validators import validate_identifier, validate_utc_datetime


class Artifact(ImmutableContractModel):
    """Metadata for an immutable report stored in a governed artifact repository."""

    artifact_id: str = Field(description="Globally unique artifact identifier")
    task_id: str = Field(description="Task that owns the artifact")
    type: ArtifactType = Field(description="Supported quality analysis report type")
    location: str = Field(description="Immutable governed repository location", min_length=1)
    media_type: str = Field(description="Artifact MIME media type", min_length=1)
    checksum: str = Field(description="Integrity checksum of artifact bytes", min_length=1)
    size_bytes: int = Field(description="Artifact size in bytes", ge=1)
    generator_version: str = Field(description="Renderer or generator version", min_length=1)
    evidence_ids: tuple[str, ...] = Field(
        description="Evidence identifiers cited by the artifact", min_length=1
    )
    created_at: datetime = Field(description="UTC time the artifact was committed")

    _validate_ids = field_validator("artifact_id", "task_id")(validate_identifier)
    _validate_created_at = field_validator("created_at")(validate_utc_datetime)
