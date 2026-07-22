"""Integration tests for the health service."""

import asyncio

from httpx import ASGITransport, AsyncClient

from copilot.api.app import app


def test_health_endpoint() -> None:
    """The health endpoint should respond successfully without external services."""

    async def request_health() -> int:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return (await client.get("/health")).status_code

    assert asyncio.run(request_health()) == 200
