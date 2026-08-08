"""Contract tests for public API response shapes."""

import asyncio

from httpx import ASGITransport, AsyncClient

from copilot.api.app import app


def test_health_response_contract() -> None:
    """The public health payload should retain its exact stable structure."""

    async def request_health() -> dict[str, str]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/health")
            return response.json()  # type: ignore[no-any-return]

    assert asyncio.run(request_health()) == {"status": "ok"}


def test_uncomposed_readiness_response_contract_is_safe() -> None:
    async def request_readiness() -> tuple[int, dict[str, object]]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/health/ready")
            return response.status_code, response.json()

    status, payload = asyncio.run(request_readiness())
    assert status == 503
    assert payload == {
        "status": "not_ready",
        "accepts_tasks": False,
        "dependencies": {"database": "not_configured"},
    }
