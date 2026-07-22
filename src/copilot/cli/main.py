"""Thin command-line interface for dry-run and deterministic workflow execution."""

from collections.abc import Callable
from typing import Annotated

import typer

from copilot.contracts import TaskStatus
from copilot.services.workflows.models import SupplierQualityCommand, WorkflowExecution

WorkflowHandler = Callable[[SupplierQualityCommand], WorkflowExecution]


def create_app(handler: WorkflowHandler | None = None) -> typer.Typer:
    """Create a CLI app with an injected application handler."""
    cli = typer.Typer(
        add_completion=False,
        no_args_is_help=True,
        context_settings={"color": False},
        rich_markup_mode=None,
    )

    @cli.command()
    def main(
        task: Annotated[
            str | None,
            typer.Option("--task", help="Task description or supplier-quality-analysis."),
        ] = None,
        supplier_id: Annotated[
            str | None, typer.Option("--supplier-id", help="Authorized supplier identifier.")
        ] = None,
        material_id: Annotated[
            str | None, typer.Option("--material-id", help="Requested material identifier.")
        ] = None,
        time_range: Annotated[
            str | None, typer.Option("--time-range", help="Explicit period in YYYY-QN form.")
        ] = None,
        dry_run: Annotated[
            bool, typer.Option("--dry-run", help="Validate input without executing a task.")
        ] = False,
    ) -> None:
        """Run the offline deterministic Supplier Quality workflow."""
        if task is not None:
            typer.echo(f"Task received:\n\n{task}")
        if dry_run:
            typer.echo("Dry run enabled.\nNo execution performed.")
            return
        if task != "supplier-quality-analysis":
            typer.echo("Unsupported task. Use supplier-quality-analysis.", err=True)
            raise typer.Exit(code=2)
        if handler is None:
            typer.echo("Workflow runtime is not composed at this entry point.", err=True)
            raise typer.Exit(code=2)
        if supplier_id is None or material_id is None or time_range is None:
            typer.echo("--supplier-id, --material-id, and --time-range are required.", err=True)
            raise typer.Exit(code=2)
        try:
            execution = handler(
                SupplierQualityCommand(
                    supplier_id=supplier_id,
                    material_id=material_id,
                    time_range=time_range,
                )
            )
        except ValueError as exc:
            typer.echo(f"Invalid input: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        typer.echo(f"Task ID: {execution.task_result.task_id}")
        typer.echo(f"Task status: {execution.task_result.final_status.value}")
        typer.echo("Step summary:")
        for result, record in zip(execution.step_results, execution.step_executions, strict=True):
            typer.echo(
                f"- {result.step_id}: {result.status.value}; "
                f"attempts={record.attempt_count}; duration_ms={record.duration_ms}"
            )
        artifact_path = execution.artifacts[0].location if execution.artifacts else "none"
        typer.echo(f"Artifact path: {artifact_path}")
        typer.echo(f"Total duration: {execution.duration_ms} ms")
        if execution.task_result.final_status is not TaskStatus.COMPLETED:
            raise typer.Exit(code=1)

    return cli


app = create_app()
