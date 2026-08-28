"""Hermetic real API composition used by browser end-to-end tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI

from copilot.api.app import create_app
from copilot.bootstrap.container import build_workflow_container
from copilot.config import Settings
from copilot.contracts.async_runtime import TaskSubmissionResponse
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.persistence.identifiers import SequentialIdentifierFactory
from copilot.security.identity import DemoIdentityProvider
from copilot.services.task_intake import NaturalLanguageTaskCommand, TrustedCallerContext
from copilot.services.task_submission import TaskSubmissionService
from tests.async_runtime_helpers import execute_accepted_task

_runtime_directory = TemporaryDirectory(prefix="copilot-frontend-e2e-")
_runtime_path = Path(_runtime_directory.name)

settings = Settings(
    app_env="test",
    database_url="sqlite:///unused-frontend-e2e-business.db",
    persistence_database_url=f"sqlite:///{_runtime_path / 'runtime.db'}",
    artifact_dir=_runtime_path / "artifacts",
    checkpoint_database_path=_runtime_path / "workflow.db",
    demo_approval_roles=("quality_analyst", "quality_data_approver"),
    log_level="WARNING",
    log_format="text",
)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Own one deterministic offline workflow runtime for the browser suite."""
    try:
        with build_workflow_container(
            settings,
            ids=SequentialIdentifierFactory(),
            sleeper=lambda _seconds: None,
            llm_provider=OfflineMockLLM(),
        ) as container:
            assert container.task_submission_service is not None
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="frontend-e2e-worker")

            class _SubmissionDriver:
                """Return 202 acceptance, then drive the hermetic Task off-request."""

                def __init__(self, delegate: TaskSubmissionService) -> None:
                    self._delegate = delegate

                def submit(
                    self,
                    command: NaturalLanguageTaskCommand,
                    caller: TrustedCallerContext,
                    *,
                    idempotency_key: str | None,
                ) -> TaskSubmissionResponse:
                    accepted = self._delegate.submit(
                        command,
                        caller,
                        idempotency_key=idempotency_key,
                    )
                    executor.submit(
                        execute_accepted_task,
                        container,
                        accepted.task_id,
                        tenant_id=caller.tenant_id,
                    )
                    return accepted

            application.state.task_service = container.task_service
            application.state.task_submission_service = _SubmissionDriver(
                container.task_submission_service
            )
            application.state.approval_service = container.approval_service
            application.state.artifact_service = container.artifact_service
            application.state.observability = container.observability
            application.state.readiness = container.readiness
            try:
                yield
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
    finally:
        _runtime_directory.cleanup()


app = create_app(
    settings=settings,
    identity_provider=DemoIdentityProvider(settings),
    lifespan=lifespan,
)

__all__ = ["app"]
