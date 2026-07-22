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
