"""Unit tests for application configuration."""

from collections.abc import Generator

import pytest

from enterprise_copilot.config import PROJECT_ROOT, ConfigurationError, get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None, None, None]:
    """Isolate the process-wide settings cache between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_load_defaults_and_normalize_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings should coerce environment values and expose an absolute artifact path."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("RAG_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("ARTIFACT_DIR", "build/test-artifacts")

    settings = get_settings()

    assert settings.log_level == "INFO"
    assert settings.max_task_steps == 10
    assert settings.rag_timeout_seconds == 45
    assert settings.artifact_path == (PROJECT_ROOT / "build/test-artifacts").resolve()
    assert settings.artifact_path.is_absolute()


def test_get_settings_returns_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated settings access should return the same instance."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")

    assert get_settings() is get_settings()


def test_missing_required_setting_has_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing database URL should produce a stable configuration error."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ConfigurationError, match="Missing required configuration: DATABASE_URL"):
        get_settings()


def test_invalid_integer_has_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An invalid integer value should fail validation rather than being accepted as text."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("RAG_TIMEOUT_SECONDS", "test")

    with pytest.raises(ConfigurationError, match="Invalid configuration: RAG_TIMEOUT_SECONDS"):
        get_settings()
