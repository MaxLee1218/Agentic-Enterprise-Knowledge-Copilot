"""Natural-language CLI parser behavior independent of the composed runtime."""

from typer.testing import CliRunner

from copilot.cli.main import create_app

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
