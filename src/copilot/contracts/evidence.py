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


class EvidenceAddResult(ImmutableContractModel):
    """Outcome of an append attempt against an evidence ledger."""

    evidence: EvidenceItem = Field(description="Canonical evidence retained by the ledger")
    created: bool = Field(description="Whether a new logical evidence record was appended")
    duplicate_of: str | None = Field(
        default=None,
        description="Existing evidence identifier when the append was deduplicated",
    )

    @model_validator(mode="after")
    def validate_duplicate_state(self) -> "EvidenceAddResult":
        """Keep creation and duplicate metadata mutually consistent."""
        if self.created and self.duplicate_of is not None:
            raise ValueError("created evidence cannot identify a duplicate")
        if not self.created and self.duplicate_of != self.evidence.evidence_id:
            raise ValueError("deduplicated evidence must identify the canonical evidence")
        return self


class EvidenceLedgerSnapshot(ImmutableContractModel):
    """Serializable append-order snapshot used to restore an evidence ledger."""

    schema_version: str = Field(default="evidence-ledger.v1", min_length=1)
    items: tuple[EvidenceItem, ...] = Field(default_factory=tuple)


class LineageEdge(ImmutableContractModel):
    """Directed parent-to-child relationship in an evidence lineage graph."""

    parent_evidence_id: str
    child_evidence_id: str

    _validate_ids = field_validator("parent_evidence_id", "child_evidence_id")(validate_identifier)


class LineageIssue(ImmutableContractModel):
    """Safe structural problem discovered while traversing evidence lineage."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    evidence_id: str | None = None
    parent_evidence_id: str | None = None


class LineageTrace(ImmutableContractModel):
    """Deterministically ordered evidence graph rooted at one evidence item."""

    task_id: str
    root_evidence_id: str
    nodes: tuple[EvidenceItem, ...]
    edges: tuple[LineageEdge, ...]
    ordered_evidence_ids: tuple[str, ...]
    is_complete: bool
    issues: tuple[LineageIssue, ...] = ()

    _validate_ids = field_validator("task_id", "root_evidence_id")(validate_identifier)
