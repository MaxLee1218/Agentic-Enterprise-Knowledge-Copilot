"""Contract tests for public API response shapes."""

from fastapi.testclient import TestClient

from enterprise_copilot.api.app import app


def test_health_response_contract() -> None:
    """The public health payload should retain its exact stable structure."""
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.json() == {"status": "ok"}

