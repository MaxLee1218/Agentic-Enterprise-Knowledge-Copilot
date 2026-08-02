"""Thin CLI for the same natural-language application service used by HTTP."""

from collections.abc import Callable
from typing import Annotated

import typer
from pydantic import ValidationError

from copilot.agent.graph import WorkflowInterrupted
from copilot.contracts import TaskStatus
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TaskIntakeValidationError,
    TaskOutputFormat,
)
from copilot.services.workflows.models import WorkflowExecution

WorkflowHandler = Callable[[NaturalLanguageTaskCommand], WorkflowExecution]


def create_app(handler: WorkflowHandler | None = None) -> typer.Typer:
    """Create a CLI app with an injected application-service adapter."""
    cli = typer.Typer(
        add_completion=False,
        no_args_is_help=True,
        context_settings={"color": False},
        rich_markup_mode=None,
    )

    @cli.command()
    def main(
        task_text: Annotated[
            str | None,
            typer.Argument(help="Natural-language enterprise task."),
        ] = None,
        task_option: Annotated[
            str | None,
            typer.Option("--task", help="Natural-language enterprise task."),
        ] = None,
        output_format: Annotated[
            TaskOutputFormat | None,
            typer.Option("--output-format", help="Frozen report format: pdf or json."),
        ] = None,
        max_steps: Annotated[
            int | None,
            typer.Option("--max-steps", min=1, help="Tighten the server step limit."),
        ] = None,
        read_only: Annotated[
            bool,
            typer.Option("--read-only", help="Explicitly request read-only execution."),
        ] = False,
        require_approval: Annotated[
            bool,
            typer.Option("--require-approval", help="Require approval even if policy does not."),
        ] = False,
        session_id: Annotated[
            str | None,
            typer.Option("--session-id", help="Non-authoritative session correlation ID."),
        ] = None,
        dry_run: Annotated[
            bool,
            typer.Option("--dry-run", help="Parse the command without executing a task."),
        ] = False,
    ) -> None:
        """Submit one natural-language task to the governed workflow."""
        if task_text is not None and task_option is not None:
            typer.echo(
                "Invalid input: provide either the positional task or --task, not both.",
                err=True,
            )
            raise typer.Exit(code=2)
        task = task_text if task_text is not None else task_option
        if task is None or not task.strip():
            typer.echo("Invalid input: task text must not be empty.", err=True)
            raise typer.Exit(code=2)
        try:
            command = NaturalLanguageTaskCommand(
                task=task,
                output_format=output_format,
                max_steps=max_steps,
                read_only=True if read_only else None,
                require_approval=True if require_approval else None,
                session_id=session_id,
                source=RequestSource.CLI,
            )
        except ValidationError as exc:
            typer.echo(f"Invalid input: {exc.errors()[0]['msg']}", err=True)
            raise typer.Exit(code=2) from exc
        if dry_run:
            typer.echo("Task input is syntactically valid.\nNo execution performed.")
            return
        if handler is None:
            typer.echo("Workflow runtime is not composed at this entry point.", err=True)
            raise typer.Exit(code=2)
        try:
            execution = handler(command)
        except TaskIntakeValidationError as exc:
            typer.echo(f"{exc.code}: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        except WorkflowInterrupted as interrupted:
            typer.echo(f"Task ID: {interrupted.task_id}")
            typer.echo(f"Trace ID: {interrupted.trace_id}")
            typer.echo(f"Task status: {interrupted.status}")
            typer.echo(f"Summary: {interrupted}")
            return
        typer.echo(f"Task ID: {execution.task_result.task_id}")
        typer.echo(f"Trace ID: {execution.trace_id}")
        typer.echo(f"Task status: {execution.task_result.final_status.value}")
        typer.echo(f"Summary: {execution.task_result.summary}")
        for error in execution.errors:
            typer.echo(f"Error: {error.error_code}: {error.message}")
            if error.error_code == "TASK_INFORMATION_MISSING":
                typer.echo(f"Missing information: {error.message}")
        artifact_path = execution.artifacts[0].location if execution.artifacts else "none"
        typer.echo(f"Artifact path: {artifact_path}")
        if execution.verification_result is not None:
            typer.echo(f"Verification status: {execution.verification_result.status.value}")
        if execution.task_result.final_status is not TaskStatus.COMPLETED:
            raise typer.Exit(code=1)

    return cli


app = create_app()

__all__ = ["WorkflowHandler", "app", "create_app"]
