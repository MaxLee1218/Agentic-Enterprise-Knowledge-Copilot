"""Version and stable-name contract tests for evaluation artifacts."""

from evaluation.config import DEFAULT_DATASET
from evaluation.contracts import EvaluationCase, EvaluationRunResult, MetricResult
from evaluation.dataset_loader import load_dataset


def test_evaluation_contract_schemas_are_versioned_and_strict() -> None:
    case_schema = EvaluationCase.model_json_schema()
    run_schema = EvaluationRunResult.model_json_schema()
    metric_schema = MetricResult.model_json_schema()

    assert "schema_version" in case_schema["properties"]
    assert "schema_version" in run_schema["properties"]
    assert "direction" in metric_schema["properties"]
    assert case_schema["additionalProperties"] is False


def test_dataset_uses_stable_evaluator_tool_and_status_names() -> None:
    dataset = load_dataset(DEFAULT_DATASET)
    registered = {
        "knowledge_search",
        "database_query",
        "analysis_engine",
        "report_generator",
    }

    assert dataset.dataset_version == "1.1.0"
    for case in dataset.cases:
        assert set(case.expected_tools.required_tools).issubset(registered)
        assert case.expected_outcome.allowed_terminal_statuses
