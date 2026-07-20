"""Integration tests for the health service."""

from fastapi.testclient import TestClient

from enterprise_copilot.api.app import app


def test_health_endpoint() -> None:
    """The health endpoint should respond successfully without external services."""
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
