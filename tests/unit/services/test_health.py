"""Liveness/readiness semantics tests."""

from copilot.services.health import ReadinessService


def test_readiness_distinguishes_required_and_optional_dependencies() -> None:
    service = ReadinessService(
        {
            "database": lambda: True,
            "artifact_storage": lambda: True,
            "rag": lambda: False,
        },
        task_dependencies=frozenset({"database", "artifact_storage", "rag"}),
    )

    snapshot = service.check()

    assert snapshot.status == "degraded"
    assert snapshot.accepts_tasks is False
    assert snapshot.dependencies["database"] == "ok"
    assert snapshot.dependencies["rag"] == "unavailable"


def test_readiness_hides_probe_exceptions() -> None:
    def fail() -> bool:
        raise RuntimeError("database-url-with-secret")

    snapshot = ReadinessService(
        {"database": fail},
        task_dependencies=frozenset({"database"}),
    ).check()

    assert snapshot.status == "not_ready"
    assert snapshot.dependencies == {"database": "unavailable"}
