"""Deterministic, offline evaluation smoke pipeline."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, TypedDict, cast

DEFAULT_DATASET_PATH: Final = Path(__file__).resolve().parent / "datasets" / "smoke_v1.json"


@dataclass(frozen=True, slots=True)
class SmokeCase:
    """One sanitized case used to prove the evaluation pipeline is operational."""

    case_id: str
    expected: str
    observed: str


@dataclass(frozen=True, slots=True)
class SmokeDataset:
    """A versioned collection of sanitized smoke cases."""

    version: str
    cases: tuple[SmokeCase, ...]


class MetricResult(TypedDict):
    """Serializable result for one deterministic metric."""

    name: str
    value: float
    passed: bool


class SmokeReport(TypedDict):
    """Metadata and results required for an evaluation report."""

    code_revision: str
    dataset_version: str
    configuration: dict[str, str]
    model_provider: str
    prompt_version: str
    timestamp: str
    metric_definitions: dict[str, str]
    metrics: list[MetricResult]
    case_count: int
    cases: list[dict[str, str]]
    known_limitations: list[str]


def load_smoke_dataset(dataset_path: Path = DEFAULT_DATASET_PATH) -> SmokeDataset:
    """Load and validate the versioned, sanitized smoke dataset."""
    raw = cast(object, json.loads(dataset_path.read_text(encoding="utf-8")))
    if not isinstance(raw, dict):
        raise ValueError("Evaluation smoke dataset root must be an object")

    version = raw.get("dataset_version")
    raw_cases = raw.get("cases")
    if not isinstance(version, str) or not version:
        raise ValueError("Evaluation smoke dataset requires a non-empty dataset_version")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("Evaluation smoke dataset must contain at least one case")

    cases: list[SmokeCase] = []
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            raise ValueError(f"Evaluation smoke case {index} must be an object")
        values = tuple(raw_case.get(field) for field in ("case_id", "expected", "observed"))
        if not all(isinstance(value, str) and value for value in values):
            raise ValueError(
                f"Evaluation smoke case {index} requires non-empty case_id, expected, and observed"
            )
        case_id, expected, observed = cast(tuple[str, str, str], values)
        cases.append(SmokeCase(case_id=case_id, expected=expected, observed=observed))
    return SmokeDataset(version=version, cases=tuple(cases))


def calculate_exact_match(cases: tuple[SmokeCase, ...]) -> MetricResult:
    """Calculate exact-match accuracy for a non-empty smoke dataset."""
    if not cases:
        raise ValueError("Evaluation smoke dataset must contain at least one case")

    matching = sum(case.expected == case.observed for case in cases)
    value = matching / len(cases)
    return {"name": "exact_match", "value": value, "passed": value == 1.0}


def build_smoke_report(dataset: SmokeDataset) -> SmokeReport:
    """Execute the metric pipeline and build a governance-complete report."""
    metric = calculate_exact_match(dataset.cases)
    return {
        "code_revision": os.environ.get("GITHUB_SHA", "local-working-tree"),
        "dataset_version": dataset.version,
        "configuration": {"mode": "offline", "metric": "exact_match"},
        "model_provider": "none",
        "prompt_version": "none",
        "timestamp": datetime.now(UTC).isoformat(),
        "metric_definitions": {
            "exact_match": "Fraction of cases whose observed value exactly matches expected"
        },
        "metrics": [metric],
        "case_count": len(dataset.cases),
        "cases": [asdict(case) for case in dataset.cases],
        "known_limitations": [
            "This smoke run validates evaluation plumbing, not product task quality."
        ],
    }


def write_smoke_report(report: SmokeReport, output_path: Path) -> None:
    """Write an evaluation report atomically enough for the local CI smoke boundary."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)


def run_smoke_evaluation(
    output_path: Path, dataset_path: Path = DEFAULT_DATASET_PATH
) -> SmokeReport:
    """Load data, run metrics, write a report, and reject a failed smoke metric."""
    dataset = load_smoke_dataset(dataset_path)
    report = build_smoke_report(dataset)
    write_smoke_report(report, output_path)
    if not all(metric["passed"] for metric in report["metrics"]):
        raise RuntimeError("Evaluation smoke metric did not meet its required threshold")
    return report
