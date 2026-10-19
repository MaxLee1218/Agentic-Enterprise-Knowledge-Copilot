"""Independent case oracle is driven by frozen scope, never product calculations."""

from evaluation.supplier_quality_pilot.cases import load_cases
from evaluation.supplier_quality_pilot.contracts import PilotSnapshot
from evaluation.supplier_quality_pilot.source import DEFAULT_SOURCE, oracle_metrics


def test_q1_and_single_supplier_oracles_use_the_case_scope() -> None:
    snapshot = PilotSnapshot.model_validate_json(DEFAULT_SOURCE.read_bytes())
    cases = {case.case_id: case for case in load_cases()}
    metrics = oracle_metrics(snapshot, cases["P04"])
    assert [(m.period, str(m.value)) for m in metrics if m.metric == "defect_rate"] == [
        ("2026-01", "0.0050"),
        ("2026-02", "0.0100"),
        ("2026-03", "0.0150"),
    ]
    assert {m.supplier_id for m in metrics} == {"S-100"}
    assert oracle_metrics(snapshot, cases["P06"]) == ()
