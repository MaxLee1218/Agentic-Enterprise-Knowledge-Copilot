"""Thin CLI for the same natural-language application service used by HTTP."""

from collections.abc import Callable
from typing import Annotated

import typer
from pydantic import ValidationError

from copilot.config import ConfigurationError
from copilot.contracts.async_runtime import TaskSubmissionResponse
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TaskIntakeValidationError,
    TaskOutputFormat,
)
from copilot.services.task_service import TaskPermissionDeniedError, TaskServiceError
from copilot.services.task_views import TaskSummaryView

SubmissionHandler = Callable[[NaturalLanguageTaskCommand, str | None], TaskSubmissionResponse]
WaitHandler = Callable[[str, float], TaskSummaryView]


def create_app(
    handler: SubmissionHandler | None = None,
    wait_handler: WaitHandler | None = None,
) -> typer.Typer:
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
        demo: Annotated[
            bool,
            typer.Option(
                "--demo",
                help=(
                    "Explicitly use the configured local demo identity "
                    "(never allowed in production)."
                ),
            ),
        ] = False,
        idempotency_key: Annotated[
            str | None,
            typer.Option("--idempotency-key", help="Tenant/caller-scoped submission key."),
        ] = None,
        wait: Annotated[
            bool,
            typer.Option("--wait", help="Poll durable Task state until terminal."),
        ] = False,
        wait_timeout: Annotated[
            float,
            typer.Option("--wait-timeout", min=0.1, help="Maximum polling time in seconds."),
        ] = 300.0,
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
                metadata={"cli_demo_identity": demo},
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
            accepted = handler(command, idempotency_key)
        except TaskIntakeValidationError as exc:
            typer.echo(f"{exc.code}: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        except ConfigurationError as exc:
            typer.echo(f"CONFIGURATION_ERROR: {exc}", err=True)
            raise typer.Exit(code=3) from exc
        except TaskPermissionDeniedError as exc:
            typer.echo(f"{exc.code}: {exc}", err=True)
            raise typer.Exit(code=5) from exc
        except TaskServiceError as exc:
            typer.echo(f"{exc.code}: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except (ConnectionError, TimeoutError) as exc:
            typer.echo(f"DEPENDENCY_UNAVAILABLE: {exc}", err=True)
            raise typer.Exit(code=4) from exc
        typer.echo(f"Task ID: {accepted.task_id}")
        typer.echo(f"Trace ID: {accepted.trace_id}")
        typer.echo(f"Task status: {accepted.task_status.value}")
        typer.echo(f"Runtime status: {accepted.runtime_status.value}")
        typer.echo(f"Status URL: {accepted.status_url}")
        if not wait:
            return
        if wait_handler is None:
            typer.echo("Wait polling is not composed at this entry point.", err=True)
            raise typer.Exit(code=2)
        try:
            completed = wait_handler(accepted.task_id, wait_timeout)
        except TimeoutError as exc:
            typer.echo(f"WAIT_TIMEOUT: {exc}", err=True)
            raise typer.Exit(code=4) from exc
        typer.echo(f"Final task status: {completed.status}")
        typer.echo(f"Final runtime status: {completed.runtime_status}")
        typer.echo(f"Summary: {completed.task_summary}")
        if completed.status != "COMPLETED":
            if completed.error_summary:
                typer.echo(f"Error: {completed.error_summary}", err=True)
            raise typer.Exit(code=1)

    return cli


app = create_app()

__all__ = ["SubmissionHandler", "WaitHandler", "app", "create_app"]
