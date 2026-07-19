"""Minimal command-line entry point for validating task input."""

from typing import Annotated

import typer

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def main(
    task: Annotated[str | None, typer.Option("--task", help="Task description to accept.")] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate input without executing a task.")
    ] = False,
) -> None:
    """Accept a task description without invoking agents or external services."""
    if task is not None:
        typer.echo(f"Task received:\n\n{task}")

    if dry_run:
        typer.echo("Dry run enabled.\nNo execution performed.")

