"""FastAPI application entry point."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Stable response contract for service health checks."""

    status: Literal["ok"]


app = FastAPI(title="Agentic Enterprise Knowledge Copilot", version="0.1.0")


@app.get("/health", response_model=HealthResponse, status_code=200)
def health() -> HealthResponse:
    """Report that the API process is available."""
    return HealthResponse(status="ok")

