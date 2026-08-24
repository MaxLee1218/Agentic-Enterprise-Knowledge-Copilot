"""Deterministic metric edge cases over real captured production executions."""

from pathlib import Path

from evaluation.config import ACCOUNTS_PAYABLE_DATASET, DEFAULT_DATASET
from evaluation.contracts import MetricStatus
from evaluation.dataset_loader import load_dataset
from evaluation.evaluators.accounts_payable import AccountsPayableEvaluator
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


def test_stage15_security_rates_cover_injection_secret_artifact_and_error_probes(
    tmp_path: Path,
) -> None:
    cases = {
        case_id: _execute(case_id, tmp_path / case_id)
        for case_id in (
            "security-user-prompt-injection",
            "security-secret-tool-output",
            "authorization-artifact-cross-user",
            "security-unsafe-tool-error",
        )
    }

    prompt = {
        metric.metric_name: metric
        for metric in SafetyEvaluator().evaluate(*cases["security-user-prompt-injection"])
    }
    secret = {
        metric.metric_name: metric
        for metric in SafetyEvaluator().evaluate(*cases["security-secret-tool-output"])
    }
    artifact = {
        metric.metric_name: metric
        for metric in SafetyEvaluator().evaluate(*cases["authorization-artifact-cross-user"])
    }
    unsafe = {
        metric.metric_name: metric
        for metric in SafetyEvaluator().evaluate(*cases["security-unsafe-tool-error"])
    }

    assert prompt["prompt_injection_success_rate"].value == 0
    assert secret["secret_leakage_rate"].value == 0
    assert artifact["artifact_authorization_failure_rate"].value == 0
    assert unsafe["unsafe_error_exposure_rate"].value == 0
    assert all(
        metrics["missing_audit_event_rate"].value == 0
        for metrics in (prompt, secret, artifact, unsafe)
    )


def test_ap_precision_is_not_available_without_both_positive_and_negative_labels(
    tmp_path: Path,
) -> None:
    dataset = load_dataset(ACCOUNTS_PAYABLE_DATASET, case_ids=("ap-mixed-quarter-json",))
    case = dataset.cases[0]
    assert case.expected_ap is not None
    without_negatives = case.model_copy(
        update={
            "expected_ap": case.expected_ap.model_copy(update={"normal_eligible_record_keys": ()})
        }
    )
    execution = EvaluationHarness(dataset_directory=dataset.path.parent).execute(case, tmp_path)

    metrics = {
        metric.metric_name: metric
        for metric in AccountsPayableEvaluator().evaluate(without_negatives, execution)
    }

    assert metrics["duplicate_detection_precision"].status is MetricStatus.NOT_AVAILABLE
    assert metrics["duplicate_detection_recall"].status is MetricStatus.NOT_AVAILABLE
    assert metrics["exception_detection_precision"].status is MetricStatus.NOT_AVAILABLE
    assert metrics["exception_detection_recall"].status is MetricStatus.NOT_AVAILABLE
