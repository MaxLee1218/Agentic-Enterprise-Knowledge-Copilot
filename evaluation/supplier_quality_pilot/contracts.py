"""Versioned inputs and observable results of the offline Supplier Quality pilot."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, computed_field, model_validator

from copilot.contracts import TaskStatus
from evaluation.contracts import EvaluationModel


class SourceRow(EvaluationModel):
    """One synthetic monthly inspection aggregate, never an expected model answer."""

    tenant_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    period: str = Field(pattern=r"^2026-(0[1-9]|1[0-2])$")
    defect_count: int = Field(ge=0)
    inspected_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "SourceRow":
        if self.defect_count > self.inspected_count:
            raise ValueError("defects cannot exceed inspections")
        return self


class PilotSnapshot(EvaluationModel):
    """Source-only snapshot; oracle fields are rejected by the strict base contract."""

    version: Literal["supplier-quality-pilot-source.v1"]
    tenant_id: str = Field(min_length=1)
    rows: tuple[SourceRow, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_p01_population(self) -> "PilotSnapshot":
        keys = [(r.tenant_id, r.supplier_id, r.period) for r in self.rows]
        if len(keys) != len(set(keys)):
            raise ValueError("source monthly rows must be unique")
        required = {
            (self.tenant_id, supplier, period)
            for supplier in ("S-100", "S-200")
            for period in ("2026-04", "2026-05", "2026-06")
        }
        if not required <= set(keys):
            raise ValueError("source must contain all six authorized Q2 months")
        return self


class OracleMetric(EvaluationModel):
    """Independently derived assertion over one reported metric and its operands."""

    metric: str
    supplier_id: str
    period: str
    value: Decimal | None
    numerator: Decimal | None
    denominator: Decimal | None
    unit: str


class PilotAssertion(EvaluationModel):
    """One stable denominator entry, including an explicit missing-value outcome."""

    assertion_id: str
    category: Literal["numeric", "scope", "evidence", "artifact", "lifecycle"]
    passed: bool
    expected: str | None = None
    actual: str | None = None


class PilotRun(EvaluationModel):
    """Auditable P01 result; offline model cost is unknown rather than mock zero."""

    schema_version: Literal["supplier-quality-pilot-run.v1"] = "supplier-quality-pilot-run.v1"
    case_id: Literal["P01"] = "P01"
    mode: Literal["offline"] = "offline"
    provider: Literal["mock"] = "mock"
    model: str
    model_cost: None = None
    started_at: datetime
    latency_ms: int = Field(ge=0)
    task_id: str | None
    task_status: TaskStatus | None
    code_revision: str | None
    code_hash: str
    git_dirty: bool
    source_hash: str
    config_hash: str
    source_version: str
    prompt_versions: tuple[str, ...]
    executed_tools: tuple[str, ...]
    artifact_paths: tuple[str, ...]
    assertions: tuple[PilotAssertion, ...]
    error_code: str | None = None
    limitations: tuple[str, ...] = (
        "Offline model with a real read-only SQLite query, analytics and JSON renderer.",
        "P01 pilot only; this is not the completed ten-scenario or a held-out benchmark.",
        "No live-model quality, provider cost or observed human-time savings measured.",
        "Evidence support covers the enumerated structured numeric facts only.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def qualified_delivery(self) -> bool:
        return bool(self.assertions) and all(item.passed for item in self.assertions)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def numeric_total(self) -> int:
        return sum(item.category == "numeric" for item in self.assertions)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def numeric_passed(self) -> int:
        return sum(item.category == "numeric" and item.passed for item in self.assertions)
