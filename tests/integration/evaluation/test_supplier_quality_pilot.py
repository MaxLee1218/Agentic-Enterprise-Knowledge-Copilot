"""P01 runs a governed task and scores the delivered artifact independently."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from copilot.contracts import ArtifactType
from copilot.services.workflows.models import WorkflowExecution
from evaluation.supplier_quality_pilot import evaluate_p01, run_p01
from evaluation.supplier_quality_pilot.contracts import PilotSnapshot
from evaluation.supplier_quality_pilot.source import DEFAULT_SOURCE


def test_offline_p01_delivers_a_report_and_independent_score(tmp_path: Path) -> None:
    run = run_p01(tmp_path)

    assert run.qualified_delivery, [item for item in run.assertions if not item.passed]
    assert run.case_id == "P01"
    assert run.mode == "offline"
    assert run.numeric_passed == run.numeric_total == 30
    report = json.loads(Path(run.artifact_paths[0]).read_text())
    assert report["scope"]["supplier_ids"] == ["S-100", "S-200"]
    assert not report["applicable_policies"]
    assert run.executed_tools == ("database_query", "analysis_engine", "report_generator")
    assert (tmp_path / "report.json").is_file()
    assert (tmp_path / "report.md").is_file()
    assert (tmp_path / "evidence.json").is_file()
    assert run.provider == "mock"
    assert run.model_cost is None


def test_disagreeing_report_evidence_indexes_fail_independent_acceptance(tmp_path: Path) -> None:
    run = run_p01(tmp_path)
    artifact_path = Path(run.artifact_paths[0])
    report = json.loads(artifact_path.read_text())
    report["evidence_references"][0]["evidence_id"] = "E-FABRICATED"
    artifact_path.write_text(json.dumps(report))
    execution = TypeAdapter(WorkflowExecution).validate_json(
        (tmp_path / "execution.json").read_bytes()
    )
    source = PilotSnapshot.model_validate_json((tmp_path / "source.json").read_bytes())

    assertions = evaluate_p01(
        source, execution, {execution.artifacts[0].artifact_id: artifact_path}
    )

    assert not next(a for a in assertions if a.assertion_id == "report_references_ledger").passed


@pytest.mark.parametrize("mutation", ["numeric", "missing", "scope", "corrupt", "lineage"])
def test_independent_acceptance_rejects_bad_deliveries(tmp_path: Path, mutation: str) -> None:
    run = run_p01(tmp_path)
    path = Path(run.artifact_paths[0])
    document = json.loads(path.read_text())
    execution = TypeAdapter(WorkflowExecution).validate_json(
        (tmp_path / "execution.json").read_bytes()
    )
    source = PilotSnapshot.model_validate_json((tmp_path / "source.json").read_bytes())
    if mutation == "numeric":
        document["key_metrics"][0]["value"] = 999
    elif mutation == "missing":
        document["key_metrics"].pop(0)
    elif mutation == "scope":
        document["scope"]["supplier_ids"] = ["S-999"]
    elif mutation == "lineage":
        calc = next(e for e in execution.evidence if e.source_type.value == "CALCULATION")
        broken = calc.model_copy(
            update={
                "source_reference": calc.source_reference.model_copy(
                    update={"input_evidence_ids": ("E-FABRICATED",)}
                ),
            }
        )
        execution = replace(
            execution,
            evidence=tuple(
                broken if e.evidence_id == calc.evidence_id else e for e in execution.evidence
            ),
        )
    path.write_text("corrupt-json" if mutation == "corrupt" else json.dumps(document))

    assertions = evaluate_p01(source, execution, {execution.artifacts[0].artifact_id: path})

    assert not all(a.passed for a in assertions)
    numbers = [a for a in assertions if a.category == "numeric"]
    assert len(numbers) == 30
    if mutation in {"numeric", "missing", "corrupt"}:
        assert any(not a.passed for a in numbers)
    if mutation == "scope":
        assert not next(a for a in assertions if a.assertion_id == "report_scope").passed
    if mutation == "lineage":
        assert not next(a for a in assertions if a.assertion_id == "calculation_lineage").passed


def test_missing_delivery_cannot_shrink_the_numeric_denominator() -> None:
    source = PilotSnapshot.model_validate_json(DEFAULT_SOURCE.read_bytes())

    assertions = evaluate_p01(source, None, {})

    numbers = [a for a in assertions if a.category == "numeric"]
    assert len(numbers) == 30
    assert not any(a.passed for a in numbers)


def test_acceptance_requires_the_committed_json_artifact_identity(tmp_path: Path) -> None:
    run = run_p01(tmp_path)
    execution = TypeAdapter(WorkflowExecution).validate_json(
        (tmp_path / "execution.json").read_bytes()
    )
    source = PilotSnapshot.model_validate_json((tmp_path / "source.json").read_bytes())
    artifact = execution.artifacts[0].model_copy(
        update={
            "type": ArtifactType.QUALITY_ANALYSIS_REPORT_PDF,
            "media_type": "application/pdf",
        }
    )

    assertions = evaluate_p01(
        source,
        replace(execution, artifacts=(artifact,)),
        {artifact.artifact_id: Path(run.artifact_paths[0])},
    )

    assert not next(a for a in assertions if a.assertion_id == "one_json_artifact").passed


def test_changed_source_flows_through_real_queries_and_independent_answers(tmp_path: Path) -> None:
    raw = json.loads(DEFAULT_SOURCE.read_text())
    for row in raw["rows"]:
        if (row["tenant_id"], row["supplier_id"], row["period"]) == (
            raw["tenant_id"],
            "S-100",
            "2026-06",
        ):
            row["defect_count"] = 40
    source_path = tmp_path / "changed-source.json"
    source_path.write_text(json.dumps(raw))

    run = run_p01(tmp_path / "run", source_path=source_path)

    assert run.qualified_delivery
    quarterly = next(
        a
        for a in run.assertions
        if a.assertion_id == "S-100/quarter_from_report_counts/defect_count"
    )
    assert quarterly.actual == quarterly.expected == "70"
    document = json.loads(Path(run.artifact_paths[0]).read_text())
    june = [
        m
        for m in document["key_metrics"]
        if m["metric"] == "defect_rate"
        and m["dimensions"] == {"supplier_id": "S-100", "period": "2026-06"}
    ]
    assert june[0]["value"] == 0.04


def test_offline_run_ignores_network_selectors_and_keeps_oracle_out_of_model_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused:unused@invalid.invalid/unused")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("KNOWLEDGE_PROVIDER", "http")
    monkeypatch.setenv("LLM_API_KEY", "PILOT-AMBIENT-SECRET")

    run = run_p01(tmp_path)

    assert run.qualified_delivery
    text = (tmp_path / "model-inputs.json").read_text()
    assert "PILOT-AMBIENT-SECRET" not in text
    calls = json.loads(text)
    assert len(calls) >= 2
    for call in calls:
        payload = json.loads(call[-1]["content"])
        assert not {"oracle", "expected_value", "source_rows", "expected_numbers"} & payload.keys()
        assert "0.0175" not in call[-1]["content"]
