"""Deterministic metric edge cases over real captured production executions."""

from pathlib import Path

from evaluation.config import DEFAULT_DATASET
from evaluation.dataset_loader import load_dataset
from evaluation.evaluators.numeric_accuracy import NumericAccuracyEvaluator
from evaluation.evaluators.safety import SafetyEvaluator
from evaluation.evaluators.task_success import TaskSuccessEvaluator
from evaluation.harness import EvaluationHarness


def _execute(case_id: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    dataset = load_dataset(DEFAULT_DATASET, case_ids=(case_id,))
    case = dataset.cases[0]
    execution = EvaluationHarness(dataset_directory=dataset.path.parent).execute(case, tmp_path)
    return case, execution


def test_numeric_evaluator_accepts_tolerance_and_null_without_nan(tmp_path: Path) -> None:
    normal_case, normal = _execute("normal-q2-analysis", tmp_path / "normal")
    zero_case, zero = _execute("analytics-zero-denominator", tmp_path / "zero")

    normal_metric = NumericAccuracyEvaluator().evaluate(normal_case, normal)[0]
    zero_metric = NumericAccuracyEvaluator().evaluate(zero_case, zero)[0]

    assert normal_metric.value == 1
    assert zero_metric.value == 1
    assert "NaN" not in "\n".join(zero.artifact_texts)
    assert "Infinity" not in "\n".join(zero.artifact_texts)


def test_safety_uses_actual_calls_and_evidence_for_attack_and_authorization(
    tmp_path: Path,
) -> None:
    attack_case, attack = _execute("prompt-injection-attempt", tmp_path / "attack")
    auth_case, auth = _execute("unauthorized-supplier-access", tmp_path / "auth")

    attack_metrics = {
        item.metric_name: item for item in SafetyEvaluator().evaluate(attack_case, attack)
    }
    auth_metrics = {item.metric_name: item for item in SafetyEvaluator().evaluate(auth_case, auth)}

    assert attack_metrics["safety_violation_rate"].value == 0
    assert attack_metrics["attack_block_rate"].value == 1
    assert auth_metrics["authorization_block_rate"].value == 1
    assert auth.tool_calls == ()


def test_expected_rejection_and_clarification_are_task_success(tmp_path: Path) -> None:
    reject_case, reject = _execute("approval-rejected", tmp_path / "reject")
    missing_case, missing = _execute("missing-time-range", tmp_path / "missing")

    assert TaskSuccessEvaluator().evaluate(reject_case, reject)[0].value == 1
    assert TaskSuccessEvaluator().evaluate(missing_case, missing)[0].value == 1
