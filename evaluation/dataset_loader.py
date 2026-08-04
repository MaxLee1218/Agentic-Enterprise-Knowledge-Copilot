"""Validated JSONL loading with stable hashes and oracle isolation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from evaluation.contracts import EvaluationCase


class DatasetValidationError(ValueError):
    """Raised when a dataset cannot safely enter the evaluation runner."""


@dataclass(frozen=True, slots=True)
class LoadedDataset:
    path: Path
    dataset_id: str
    dataset_version: str
    dataset_hash: str
    fixture_hash: str
    cases: tuple[EvaluationCase, ...]


def load_dataset(
    path: Path,
    *,
    case_ids: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
) -> LoadedDataset:
    """Load, validate, filter, and deterministically order one JSONL dataset."""
    resolved = path.resolve()
    if not resolved.is_file():
        raise DatasetValidationError(f"Dataset does not exist: {path}")
    raw_bytes = resolved.read_bytes()
    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(raw_bytes.decode("utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            case = EvaluationCase.model_validate_json(raw_line)
        except (ValidationError, ValueError) as exc:
            raise DatasetValidationError(f"Invalid dataset line {line_number}: {exc}") from exc
        if case.case_id in seen:
            raise DatasetValidationError(f"Duplicate case_id: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise DatasetValidationError("Dataset must contain at least one case")
    dataset_ids = {case.dataset_id for case in cases}
    dataset_versions = {case.dataset_version for case in cases}
    if len(dataset_ids) != 1 or len(dataset_versions) != 1:
        raise DatasetValidationError("All cases must share dataset_id and dataset_version")
    for case in cases:
        _validate_fixtures(case, resolved.parent)
    selected = [
        case
        for case in cases
        if case.enabled
        and (not case_ids or case.case_id in case_ids)
        and (not tags or any(tag in case.tags for tag in tags))
    ]
    if not selected:
        raise DatasetValidationError("No enabled evaluation cases matched the filters")
    selected.sort(key=lambda item: item.case_id)
    fixture_hash = _fixture_hash(cases, resolved.parent)
    return LoadedDataset(
        path=resolved,
        dataset_id=next(iter(dataset_ids)),
        dataset_version=next(iter(dataset_versions)),
        dataset_hash=f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}",
        fixture_hash=fixture_hash,
        cases=tuple(selected),
    )


def agent_task_payload(case: EvaluationCase) -> dict[str, object]:
    """Return only caller-visible input; expected/oracle fields are intentionally absent."""
    return {
        "task": case.task_input.raw_input,
        "output_format": case.task_input.output_format,
        "read_only": case.task_input.read_only,
        "require_approval": case.task_input.require_approval,
        "max_steps": case.task_input.max_steps,
        "metadata": case.task_input.metadata,
    }


def _validate_fixtures(case: EvaluationCase, dataset_directory: Path) -> None:
    fixture_root = (dataset_directory / "fixtures").resolve()
    for reference in case.fixture_refs:
        pure = PurePosixPath(reference)
        if pure.is_absolute() or ".." in pure.parts:
            raise DatasetValidationError(f"Unsafe fixture reference in {case.case_id}: {reference}")
        candidate = (fixture_root / Path(*pure.parts)).resolve()
        if fixture_root not in candidate.parents or not candidate.is_file():
            raise DatasetValidationError(f"Fixture does not exist for {case.case_id}: {reference}")


def _fixture_hash(cases: list[EvaluationCase], dataset_directory: Path) -> str:
    digest = hashlib.sha256()
    fixture_root = dataset_directory / "fixtures"
    for reference in sorted({item for case in cases for item in case.fixture_refs}):
        digest.update(reference.encode("utf-8"))
        digest.update((fixture_root / reference).read_bytes())
    return f"sha256:{digest.hexdigest()}"


def canonical_hash(value: object) -> str:
    """Hash a stable JSON value without relying on object repr."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "DatasetValidationError",
    "LoadedDataset",
    "agent_task_payload",
    "canonical_hash",
    "load_dataset",
]
