"""Integration tests for the health service."""

import asyncio

from httpx import ASGITransport, AsyncClient

from copilot.api.app import app, create_app
from copilot.services.health import ReadinessService


def test_health_endpoint() -> None:
    """The health endpoint should respond successfully without external services."""

    async def request_health() -> int:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return (await client.get("/health")).status_code

    assert asyncio.run(request_health()) == 200


def test_liveness_and_readiness_have_distinct_status_codes() -> None:
    application = create_app(
        readiness=ReadinessService(
            {"database": lambda: True, "artifact_storage": lambda: True},
            task_dependencies=frozenset({"database", "artifact_storage"}),
        )
    )

    async def request_health() -> tuple[int, int, dict[str, object]]:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            live = await client.get("/health/live")
            ready = await client.get("/health/ready")
            return live.status_code, ready.status_code, ready.json()

    live_status, ready_status, payload = asyncio.run(request_health())
    assert live_status == 200
    assert ready_status == 200
    assert payload["status"] == "ready"
    assert payload["accepts_tasks"] is True
