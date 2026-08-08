"""Production API composition root."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from copilot.api.app import create_app
from copilot.bootstrap.container import build_application
from copilot.config import get_settings
from copilot.security.identity import build_identity_provider

settings = get_settings()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Own and close the composed runtime for the API process lifetime."""
    with build_application(settings) as container:
        application.state.task_service = container.task_service
        application.state.approval_service = container.approval_service
        application.state.artifact_service = container.artifact_service
        application.state.settings = settings
        application.state.identity_provider = build_identity_provider(settings)
        application.state.observability = container.observability
        application.state.readiness = container.readiness
        yield


app = create_app(settings=settings, lifespan=lifespan)

__all__ = ["app"]
