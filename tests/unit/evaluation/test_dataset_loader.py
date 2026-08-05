"""Dataset validation and oracle-isolation tests."""

import json
from pathlib import Path

import pytest

from evaluation.config import DEFAULT_DATASET
from evaluation.dataset_loader import DatasetValidationError, agent_task_payload, load_dataset


def test_supplier_dataset_loads_with_stable_hash_and_unique_cases() -> None:
    first = load_dataset(DEFAULT_DATASET)
    second = load_dataset(DEFAULT_DATASET)

    assert len(first.cases) == 30
    assert first.dataset_hash == second.dataset_hash
    assert first.fixture_hash == second.fixture_hash
    assert len({case.case_id for case in first.cases}) == len(first.cases)


def test_oracle_fields_never_enter_agent_task_payload() -> None:
    case = load_dataset(DEFAULT_DATASET, case_ids=("normal-q2-analysis",)).cases[0]

    payload = agent_task_payload(case)

    assert set(payload) == {
        "task",
        "output_format",
        "read_only",
        "require_approval",
        "max_steps",
        "metadata",
    }
    assert "expected_outcome" not in payload
    assert "expected_numbers" not in payload


def test_duplicate_case_id_is_rejected(tmp_path: Path) -> None:
    line = DEFAULT_DATASET.read_text(encoding="utf-8").splitlines()[0]
    dataset = tmp_path / "duplicate.jsonl"
    dataset.write_text(f"{line}\n{line}\n", encoding="utf-8")

    with pytest.raises(DatasetValidationError, match="Duplicate case_id"):
        load_dataset(dataset)


def test_missing_case_id_is_rejected(tmp_path: Path) -> None:
    dataset = tmp_path / "missing.jsonl"
    dataset.write_text('{"dataset_id":"x"}\n', encoding="utf-8")

    with pytest.raises(DatasetValidationError, match="Invalid dataset line"):
        load_dataset(dataset)


@pytest.mark.parametrize(
    ("field_path", "invalid_value"),
    (
        (("category",), "not-a-category"),
        (("expected_outcome", "allowed_terminal_statuses"), ["NOT_A_STATUS"]),
        (("expected_numbers", 0, "absolute_tolerance"), "-0.1"),
    ),
)
def test_invalid_enum_and_tolerance_values_are_rejected(
    tmp_path: Path,
    field_path: tuple[str | int, ...],
    invalid_value: object,
) -> None:
    payload = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8").splitlines()[0])
    target = payload
    for component in field_path[:-1]:
        target = target[component]
    target[field_path[-1]] = invalid_value
    dataset = tmp_path / "invalid.jsonl"
    dataset.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(DatasetValidationError, match="Invalid dataset line"):
        load_dataset(dataset)


def test_unsafe_fixture_path_is_rejected(tmp_path: Path) -> None:
    payload = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8").splitlines()[0])
    payload["fixture_refs"] = ["../secret.json"]
    dataset = tmp_path / "unsafe.jsonl"
    dataset.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(DatasetValidationError, match="Unsafe fixture reference"):
        load_dataset(dataset)
