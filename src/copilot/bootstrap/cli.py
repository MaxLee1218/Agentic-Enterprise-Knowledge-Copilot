"""Composed CLI entry point for the offline deterministic workflow."""

from copilot.bootstrap.container import build_workflow_container
from copilot.cli.main import create_app
from copilot.config import get_settings
from copilot.services.workflows.models import SupplierQualityCommand, WorkflowExecution


def _run(command: SupplierQualityCommand) -> WorkflowExecution:
    with build_workflow_container(get_settings()) as container:
        return container.service.execute(command)


app = create_app(_run)
