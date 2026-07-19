"""Shared foundations for versionable domain contracts."""

from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel

JsonMapping: TypeAlias = dict[str, JsonValue]


class ContractModel(BaseModel):
    """Base model that rejects unknown fields and validates every assignment."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, populate_by_name=True)


class ImmutableContractModel(ContractModel):
    """Base model for append-only or snapshot domain values."""

    model_config = ConfigDict(
        extra="forbid", validate_assignment=True, populate_by_name=True, frozen=True
    )


class JsonObject(RootModel[JsonMapping]):
    """Serializable JSON object used only at explicit schema or payload boundaries."""

    model_config = ConfigDict(frozen=True)
    root: JsonMapping = Field(description="JSON-compatible object boundary payload")
