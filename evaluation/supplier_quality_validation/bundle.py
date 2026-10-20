"""Read-only freeze verification and independent source arithmetic."""

import hashlib
import logging
import sqlite3
from contextlib import closing
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from evaluation.contracts import EvaluationModel
from evaluation.supplier_quality_pilot.contracts import OracleMetric, SourceRow
from evaluation.supplier_quality_pilot.policy import PolicySnapshot
from evaluation.supplier_quality_pilot.source import ratio

DEFAULT_BUNDLE = Path(__file__).parents[1] / "datasets/supplier_quality_validation_v1"
FILES = {"source.json", "cases.jsonl", "oracle.json", "policy.json", "protocol.json"}
LOGGER = logging.getLogger(__name__)


class ValidationCase(EvaluationModel):
    """Expected scope belongs to the evaluator, never the trusted caller or model prompt."""

    case_id: str = Field(pattern=r"^V(0[1-9]|1[0-9]|20)$")
    request: str = Field(min_length=1)
    group: Literal["direct", "clarification", "safety"]
    suppliers: tuple[str, ...] = Field(min_length=1)
    year: Literal[2026] = 2026
    quarter: int = Field(ge=1, le=4)
    output_format: Literal["json", "pdf"]
    policy: bool = False
    scripted_answer: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_scope(self) -> "ValidationCase":
        if len(set(self.suppliers)) != len(self.suppliers):
            raise ValueError("duplicate supplier scope")
        authorized = set(self.suppliers) <= {"S-100", "S-200"}
        if authorized == (self.group == "safety"):
            raise ValueError("case group does not match supplier authorization")
        if (self.scripted_answer is not None) != (self.group == "clarification"):
            raise ValueError("only clarification cases have a scripted answer")
        return self

    @property
    def start_period(self) -> str:
        """Inclusive first month of the evaluator's declared quarter."""
        return f"{self.year}-{(self.quarter - 1) * 3 + 1:02}"

    @property
    def end_period(self) -> str:
        """Inclusive last month; no product date helper is consulted."""
        return f"{self.year}-{self.quarter * 3:02}"


class ValidationSource(EvaluationModel):
    """Synthetic facts only; strict fields prevent embedding expected answers."""

    version: Literal["supplier-quality-validation-source.v1"]
    tenant_id: Literal["TENANT-VALIDATION"]
    rows: tuple[SourceRow, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_rows(self) -> "ValidationSource":
        keys = [(r.tenant_id, r.supplier_id, r.period) for r in self.rows]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate source month")
        if not any(r.tenant_id != self.tenant_id for r in self.rows) or not any(
            r.tenant_id == self.tenant_id and r.supplier_id == "S-999" for r in self.rows
        ):
            raise ValueError("leakage sentinels missing")
        return self


class QuarterOracle(EvaluationModel):
    """Check ratios of aggregate counts without requiring a new product aggregate output."""

    supplier_id: str
    defect_count: int
    inspected_count: int
    defect_rate: Decimal | None


class CaseOracle(EvaluationModel):
    """Literal expected values and stable denominator entries."""

    case_id: str
    metrics: tuple[OracleMetric, ...]
    quarterly: tuple[QuarterOracle, ...]
    numeric_ids: tuple[str, ...]
    fact_ids: tuple[str, ...]


class OracleFile(EvaluationModel):
    """Versioned literal answers in the same fixed order as the requests."""

    version: Literal["supplier-quality-validation-oracle.v1"]
    cases: tuple[CaseOracle, ...]


class ValidationProtocol(EvaluationModel):
    """Predeclared interpretation of a future run, not a run result or spending authority."""

    version: Literal["supplier-quality-validation-protocol.v1"]
    report_denominator: Literal[16]
    safety_denominator: Literal[4]
    numeric_denominator: Literal[317]
    fact_denominator: Literal[256]
    whole_case_attempts: Literal[1]
    independently_blinded: Literal[False]
    human_time_measured: Literal[False]
    authoring_disclosure: str = Field(min_length=1)
    qualified_requires: tuple[str, ...] = Field(min_length=1)
    safety_requires: tuple[str, ...] = Field(min_length=1)
    clarification_requires: tuple[str, ...] = Field(min_length=1)
    execution_rules: tuple[str, ...] = Field(min_length=1)


class FreezeManifest(EvaluationModel):
    """Content identity and publication provenance, never a signature or execution result."""

    version: Literal["supplier-quality-validation-freeze.v1"]
    frozen_at: AwareDatetime
    reference_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    publication: Literal["dataset-only"]
    files: dict[str, str]

    @model_validator(mode="after")
    def validate_utc(self) -> "FreezeManifest":
        if self.frozen_at.utcoffset() != timedelta(0):
            raise ValueError("freeze timestamp must use UTC")
        return self


class ValidationBundle(EvaluationModel):
    """Verified data available to a future runner, never automatically dispatched."""

    manifest: FreezeManifest
    freeze_hash: str
    source: ValidationSource
    cases: tuple[ValidationCase, ...]
    oracles: tuple[CaseOracle, ...]
    policy: PolicySnapshot
    protocol: ValidationProtocol
    numeric_total: int
    fact_total: int


def _derive(
    source: ValidationSource, case: ValidationCase
) -> tuple[list[OracleMetric], list[QuarterOracle]]:
    """SELECT only evaluator source rows; never import production SQL or analytics."""
    if case.group == "safety":
        return [], []
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("CREATE TABLE facts (tenant, supplier, month, defects, inspected)")
        connection.executemany(
            "INSERT INTO facts VALUES (?, ?, ?, ?, ?)",
            [
                (r.tenant_id, r.supplier_id, r.period, r.defect_count, r.inspected_count)
                for r in source.rows
            ],
        )
        rows = connection.execute(
            "SELECT supplier, month, SUM(defects), SUM(inspected) FROM facts "
            f"WHERE tenant = ? AND supplier IN ({','.join('?' for _ in case.suppliers)}) "
            "AND month BETWEEN ? AND ? GROUP BY supplier, month ORDER BY supplier, month",
            (source.tenant_id, *case.suppliers, case.start_period, case.end_period),
        ).fetchall()
    metrics: list[OracleMetric] = []
    previous: dict[str, Decimal | None] = {}
    totals: dict[str, tuple[int, int]] = {}
    for supplier, month, defects, inspected in rows:
        rate = ratio(defects, inspected)
        prior = previous.get(supplier)
        trend = rate - prior if rate is not None and prior is not None else None
        for name, value, numerator, denominator, unit in (
            ("defect_count", Decimal(defects), Decimal(defects), None, "count"),
            ("inspected_count", Decimal(inspected), Decimal(inspected), None, "count"),
            ("defect_rate", rate, Decimal(defects), Decimal(inspected), "ratio"),
            ("period_over_period_trend", trend, rate, prior, "ratio_delta"),
        ):
            metrics.append(
                OracleMetric(
                    metric=name,
                    supplier_id=supplier,
                    period=month,
                    value=value,
                    numerator=numerator,
                    denominator=denominator,
                    unit=unit,
                )
            )
        previous[supplier] = rate
        d, n = totals.get(supplier, (0, 0))
        totals[supplier] = (d + defects, n + inspected)
    quarterly = [
        QuarterOracle(supplier_id=s, defect_count=d, inspected_count=n, defect_rate=ratio(d, n))
        for s, (d, n) in sorted(totals.items())
    ]
    return metrics, quarterly


def verify_bundle(directory: Path = DEFAULT_BUNDLE) -> ValidationBundle:
    """Reject changed data/answers; no mutation, Task execution, settings or HTTP calls."""
    manifest_bytes = (directory / "freeze.json").read_bytes()
    manifest = FreezeManifest.model_validate_json(manifest_bytes)
    if set(manifest.files) != FILES:
        raise ValueError("freeze requires the exact five input files")
    for name, expected_hash in manifest.files.items():
        if "sha256:" + hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected_hash:
            raise ValueError(f"frozen file changed: {name}")
    source = ValidationSource.model_validate_json((directory / "source.json").read_bytes())
    cases = tuple(
        ValidationCase.model_validate_json(line)
        for line in (directory / "cases.jsonl").read_text().splitlines()
        if line.strip()
    )
    if tuple(c.case_id for c in cases) != tuple(f"V{i:02}" for i in range(1, 21)):
        raise ValueError("validation requires V01 through V20 once, in order")
    if tuple(c.group for c in cases) != ("direct",) * 12 + ("clarification",) * 4 + ("safety",) * 4:
        raise ValueError("fixed report/clarification/safety population changed")
    oracles = OracleFile.model_validate_json((directory / "oracle.json").read_bytes()).cases
    if tuple(o.case_id for o in oracles) != tuple(c.case_id for c in cases):
        raise ValueError("oracle population mismatch")
    for case, oracle in zip(cases, oracles, strict=True):
        metrics, quarterly = _derive(source, case)
        if tuple(metrics) != oracle.metrics or tuple(quarterly) != oracle.quarterly:
            raise ValueError(f"source/oracle mismatch: {case.case_id}")
        keys = tuple(f"{m.supplier_id}/{m.period}/{m.metric}" for m in metrics)
        numeric_ids = keys + tuple(
            f"{q.supplier_id}/quarter_from_report_counts/{metric}"
            for q in quarterly
            for metric in ("defect_count", "inspected_count", "defect_rate")
        )
        facts = tuple(f"support/{key}" for key in keys)
        if case.group != "safety":
            numeric_ids = numeric_ids or ("empty/no_fabricated_metrics",)
            facts = facts or ("support/no_records",)
            if case.policy:
                facts += ("support/policy_method_and_limit",)
        if oracle.numeric_ids != numeric_ids or oracle.fact_ids != facts:
            raise ValueError(f"assertion identities changed: {case.case_id}")
    protocol = ValidationProtocol.model_validate_json((directory / "protocol.json").read_bytes())
    numeric_total = sum(len(o.numeric_ids) for o in oracles)
    fact_total = sum(len(o.fact_ids) for o in oracles)
    if (numeric_total, fact_total) != (protocol.numeric_denominator, protocol.fact_denominator):
        raise ValueError("frozen denominators changed")
    bundle = ValidationBundle(
        manifest=manifest,
        freeze_hash="sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
        source=source,
        cases=cases,
        oracles=oracles,
        policy=PolicySnapshot.model_validate_json((directory / "policy.json").read_bytes()),
        protocol=protocol,
        numeric_total=numeric_total,
        fact_total=fact_total,
    )
    LOGGER.info(
        "validation.freeze.verified",
        extra={
            "freeze_hash": bundle.freeze_hash,
            "cases": len(cases),
            "numeric_total": numeric_total,
            "fact_total": fact_total,
        },
    )
    return bundle
