"""Evidence records and source-lineage contracts."""

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from copilot.contracts.base import ImmutableContractModel, JsonObject
from copilot.contracts.enums import EvidenceType
from copilot.contracts.validators import validate_identifier, validate_utc_datetime


class EvidenceSourceReference(ImmutableContractModel):
    """Minimal stable source identity and lineage for an evidence item."""

    reference: JsonObject = Field(
        description="Type-specific document, query, or calculation reference"
    )
    input_evidence_ids: tuple[str, ...] = Field(
        default_factory=tuple, description="Input evidence used by a calculation"
    )


class EvidenceContent(ImmutableContractModel):
    """Minimized evidence content with classification and integrity checksum."""

    data: JsonObject = Field(description="Minimal structured facts or excerpt")
    classification: str = Field(description="Enterprise data classification", min_length=1)
    checksum: str = Field(description="Integrity checksum of normalized content", min_length=1)


class EvidenceItem(ImmutableContractModel):
    """Immutable evidence unit connecting a claim to an approved source."""

    evidence_id: str = Field(description="Globally unique evidence identifier")
    task_id: str = Field(description="Task that owns the evidence")
    step_id: str = Field(description="Step that produced the evidence")
    tool_call_id: str = Field(description="Tool invocation that produced the evidence")
    source_type: EvidenceType = Field(description="Document, database, or calculation source")
    source_reference: EvidenceSourceReference = Field(description="Stable source and lineage data")
    content: EvidenceContent = Field(description="Minimized classified evidence content")
    timestamp: datetime = Field(description="UTC evidence capture or calculation time")

    _validate_ids = field_validator("evidence_id", "task_id", "step_id", "tool_call_id")(
        validate_identifier
    )
    _validate_timestamp = field_validator("timestamp")(validate_utc_datetime)

    @model_validator(mode="after")
    def validate_calculation_lineage(self) -> "EvidenceItem":
        """Prevent calculation evidence from becoming an untraceable source island."""
        if (
            self.source_type is EvidenceType.CALCULATION
            and not self.source_reference.input_evidence_ids
        ):
            raise ValueError("calculation evidence must reference input evidence")
        return self
