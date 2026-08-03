"""Natural-language CLI parser behavior independent of the composed runtime."""

from typer.testing import CliRunner

from copilot.cli.main import create_app
from copilot.config import ConfigurationError
from copilot.services.task_service import TaskPermissionDeniedError, TaskServiceError
from copilot.services.workflows.models import WorkflowExecution

runner = CliRunner()
app = create_app()


def test_position_and_option_task_forms_accept_unicode() -> None:
    positional = runner.invoke(app, ["分析“2026 Q2”供应商质量。", "--dry-run"])
    option = runner.invoke(
        app,
        ["--task", "Analysiere die Qualität im 2. Quartal 2026.", "--dry-run"],
    )

    assert positional.exit_code == 0
    assert option.exit_code == 0
    assert "syntactically valid" in positional.stdout


def test_conflicting_task_sources_are_rejected() -> None:
    result = runner.invoke(app, ["one", "--task", "two", "--dry-run"])

    assert result.exit_code == 2
    assert "either the positional task or --task" in result.output


def test_invalid_output_format_is_rejected_by_typer() -> None:
    result = runner.invoke(
        app,
        ["Analyze Q2 2026 supplier quality.", "--output-format", "markdown", "--dry-run"],
    )

    assert result.exit_code == 2
    assert "pdf" in result.output
    assert "json" in result.output


def test_missing_task_uses_cli_input_exit_code() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 2


def test_runtime_failures_use_stable_exit_codes_and_stderr() -> None:
    def configuration_failure(_command: object) -> WorkflowExecution:
        raise ConfigurationError("invalid test configuration")

    def dependency_failure(_command: object) -> WorkflowExecution:
        raise ConnectionError("controlled unavailable dependency")

    def task_failure(_command: object) -> WorkflowExecution:
        raise TaskServiceError(
            "TASK_FAILED",
            "Controlled task failure.",
            status_code=500,
            task_id="T-001",
        )

    def permission_failure(_command: object) -> WorkflowExecution:
        raise TaskPermissionDeniedError("T-001")

    for handler, expected in (
        (configuration_failure, 3),
        (dependency_failure, 4),
        (task_failure, 1),
        (permission_failure, 5),
    ):
        result = runner.invoke(create_app(handler), ["Analyze Q2 2026 supplier quality."])
        assert result.exit_code == expected
        assert result.stdout == ""
