"""The frozen source/oracle boundary, without running the product against new cases."""

import hashlib
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from evaluation.supplier_quality_validation import DEFAULT_BUNDLE, verify_bundle


def test_frozen_validation_has_fixed_denominators_and_independent_worked_answers() -> None:
    bundle = verify_bundle()

    assert len(bundle.cases) == 20
    assert bundle.numeric_total == 317
    assert bundle.fact_total == 256
    oracle = bundle.oracles[0]
    metrics = {(m.metric, m.supplier_id, m.period): m for m in oracle.metrics}
    assert metrics["defect_rate", "S-100", "2026-01"].value == Decimal("0.0312")
    assert metrics["period_over_period_trend", "S-100", "2026-02"].value == Decimal("-0.0250")
    assert metrics["defect_rate", "S-200", "2026-02"].value is None
    assert metrics["period_over_period_trend", "S-200", "2026-03"].value is None
    assert oracle.quarterly[0].defect_count == 5
    assert oracle.quarterly[0].inspected_count == 352
    assert oracle.quarterly[0].defect_rate == Decimal("0.0142")
    assert not bundle.oracles[9].metrics  # Q4 has no authorized records.
    assert not bundle.oracles[16].numeric_ids  # Refusals do not inflate report accuracy.


@pytest.fixture
def copy_of_bundle(tmp_path: Path) -> Path:
    return Path(shutil.copytree(DEFAULT_BUNDLE, tmp_path / "bundle"))


def test_freeze_timestamp_must_use_utc(copy_of_bundle: Path) -> None:
    path = copy_of_bundle / "freeze.json"
    manifest = json.loads(path.read_text())
    manifest["frozen_at"] = "2026-10-20T10:00:00+02:00"
    path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="UTC"):
        verify_bundle(copy_of_bundle)


def _rebind(directory: Path, name: str) -> None:
    """Only tests re-sign mutations, to test semantic validation beyond file hashes."""
    path = directory / "freeze.json"
    manifest = json.loads(path.read_text())
    manifest["files"][name] = (
        "sha256:" + hashlib.sha256((directory / name).read_bytes()).hexdigest()
    )
    path.write_text(json.dumps(manifest))


def test_changed_file_is_rejected_without_mutating_inputs(copy_of_bundle: Path) -> None:
    path = copy_of_bundle / "source.json"
    path.write_bytes(path.read_bytes() + b"\n")
    before = {p.name: p.read_bytes() for p in copy_of_bundle.iterdir()}

    with pytest.raises(ValueError, match="frozen file changed: source.json"):
        verify_bundle(copy_of_bundle)

    assert before == {p.name: p.read_bytes() for p in copy_of_bundle.iterdir()}


@pytest.mark.parametrize("mutation", ["value", "operand", "unit", "null", "denominator"])
def test_rehashed_incorrect_oracle_still_fails_source_check(
    copy_of_bundle: Path, mutation: str
) -> None:
    path = copy_of_bundle / "oracle.json"
    oracle = json.loads(path.read_text())
    target = oracle["cases"][0]
    if mutation == "value":
        target["metrics"][2]["value"] = "0.0313"  # 1/32 uses half-even, not half-up.
    elif mutation == "operand":
        target["metrics"][2]["denominator"] = "33"
    elif mutation == "unit":
        target["metrics"][2]["unit"] = "percent"
    elif mutation == "null":
        target["metrics"][3]["value"] = "0"  # First-month trend has no baseline.
    else:
        target["numeric_ids"].pop()
    path.write_text(json.dumps(oracle))
    _rebind(copy_of_bundle, "oracle.json")

    with pytest.raises(ValueError, match="source/oracle mismatch|assertion identities changed"):
        verify_bundle(copy_of_bundle)


@pytest.mark.parametrize("mutation", ["missing", "reordered", "duplicate", "scope"])
def test_rehashed_invalid_case_population_is_rejected(copy_of_bundle: Path, mutation: str) -> None:
    path = copy_of_bundle / "cases.jsonl"
    cases = [json.loads(line) for line in path.read_text().splitlines()]
    if mutation == "missing":
        cases.pop()
    elif mutation == "reordered":
        cases.reverse()
    elif mutation == "duplicate":
        cases[1] = cases[0]
    else:
        cases[0]["suppliers"] = ["S-999"]
    path.write_text("".join(json.dumps(case) + "\n" for case in cases))
    _rebind(copy_of_bundle, "cases.jsonl")

    with pytest.raises(ValueError, match="once, in order|authorization"):
        verify_bundle(copy_of_bundle)


def test_cli_only_verifies_inputs_and_never_creates_run_results(
    copy_of_bundle: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from evaluation.supplier_quality_validation.__main__ import main

    monkeypatch.setenv("LLM_PROVIDER", "not-a-provider")
    monkeypatch.setenv("LLM_API_KEY", "synthetic-not-a-secret")
    before = {p.name: p.read_bytes() for p in copy_of_bundle.iterdir()}

    assert main(["--bundle", str(copy_of_bundle)]) == 0
    output = capsys.readouterr().out
    assert "numeric /317, facts /256" in output
    assert "no delivery results measured" in output
    assert before == {p.name: p.read_bytes() for p in copy_of_bundle.iterdir()}
    (copy_of_bundle / "policy.json").unlink()
    assert main(["--bundle", str(copy_of_bundle)]) == 1
    assert not (copy_of_bundle / "suite.json").exists()
