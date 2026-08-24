"""Unit tests for application configuration."""

from collections.abc import Generator
from pathlib import Path

import pytest

from copilot.config import PROJECT_ROOT, ConfigurationError, Settings, get_settings


@pytest.fixture(autouse=True)
def isolate_settings_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Generator[None, None, None]:
    """Isolate the settings cache and local dotenv source between tests."""
    monkeypatch.setitem(Settings.model_config, "env_file", tmp_path / ".env")
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
    assert settings.max_task_steps == 14
    assert settings.rag_timeout_seconds == 45
    assert settings.rag_max_attempts == 3
    assert settings.rag_retry_base_delay_seconds == 0.2
    assert str(settings.rag_base_url) == "http://127.0.0.1:8000/"
    assert settings.database_statement_timeout_seconds == 8
    assert settings.workflow_max_retries == 2
    assert settings.workflow_retry_delay_seconds == 0
    assert settings.workflow_engine == "langgraph"
    assert settings.checkpoint_enabled is True
    assert settings.max_replan_count == 2
    assert settings.max_total_execution_seconds == 300
    assert settings.graph_recursion_limit == 100
    assert settings.checkpoint_database_path.is_absolute()
    assert (
        settings.ap_policy_bundle_dir
        == (PROJECT_ROOT / "data/policies/accounts_payable/v1").resolve()
    )
    assert settings.policy_snapshot_dir == (PROJECT_ROOT / "data/policy-snapshots").resolve()
    assert settings.artifact_path == (PROJECT_ROOT / "build/test-artifacts").resolve()
    assert settings.artifact_path.is_absolute()


def test_example_governance_limits_match_runtime_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the safe template aligned with limits shared by both domain profiles."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    settings = get_settings()
    template = {
        key: value
        for line in (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
        for key, value in (line.split("=", maxsplit=1),)
    }

    assert int(template["MAX_DATABASE_ROWS"]) == settings.max_database_rows
    assert int(template["REPORT_MAX_SIZE_BYTES"]) == settings.report_max_size_bytes
    assert int(template["MAX_TASK_STEPS"]) == settings.max_task_steps


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


def test_negative_workflow_retry_configuration_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry count and delay must remain non-negative startup configuration."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("WORKFLOW_MAX_RETRIES", "-1")

    with pytest.raises(ConfigurationError, match="WORKFLOW_MAX_RETRIES"):
        get_settings()


def test_invalid_rag_attempt_configuration_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP attempt count must stay within the frozen three-attempt budget."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("RAG_MAX_ATTEMPTS", "0")

    with pytest.raises(ConfigurationError, match="RAG_MAX_ATTEMPTS"):
        get_settings()


def test_invalid_rag_trace_header_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed configured header names must fail before an HTTP request."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("RAG_TRACE_HEADER", "bad header")

    with pytest.raises(ConfigurationError, match="RAG_TRACE_HEADER"):
        get_settings()


def test_mock_llm_requires_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    settings = get_settings()

    assert settings.llm_api_key is None
    assert settings.llm_model == "deepseek-chat"


def test_deepseek_api_key_is_required_only_at_real_provider_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    settings = get_settings()

    with pytest.raises(ConfigurationError, match="LLM_API_KEY"):
        settings.require_llm_api_key()


def test_llm_secret_repr_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "not-visible-in-repr"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", secret)

    settings = get_settings()

    assert secret not in repr(settings)
    assert settings.require_llm_api_key().get_secret_value() == secret


def test_database_statement_timeout_cannot_exceed_frozen_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("DATABASE_STATEMENT_TIMEOUT_SECONDS", "9")

    with pytest.raises(ConfigurationError, match="DATABASE_STATEMENT_TIMEOUT_SECONDS"):
        get_settings()


def test_local_persistence_defaults_to_checkpoint_sqlite_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///enterprise.db")
    monkeypatch.delenv("PERSISTENCE_DATABASE_URL", raising=False)
    settings = get_settings()

    assert settings.effective_persistence_database_url == (
        f"sqlite:///{settings.checkpoint_database_path}"
    )
    assert settings.database_provider == "mock"
    assert settings.knowledge_provider == "mock"


def test_production_requires_postgres_migrations_and_real_boundary_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///enterprise.db")
    monkeypatch.setenv("PERSISTENCE_DATABASE_URL", "sqlite:///copilot.db")

    with pytest.raises(ConfigurationError, match="SETTINGS"):
        get_settings()


def test_valid_production_profile_is_strict_and_separates_databases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PERSISTENCE_DATABASE_URL", "postgresql+psycopg://user:secret@db/copilot")
    monkeypatch.setenv("PERSISTENCE_AUTO_CREATE_SCHEMA", "false")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://readonly:secret@business/quality")
    monkeypatch.setenv("DATABASE_PROVIDER", "sqlalchemy")
    monkeypatch.setenv("KNOWLEDGE_PROVIDER", "http")
    monkeypatch.setenv("RAG_BASE_URL", "https://rag.internal.example")
    monkeypatch.setenv("IDENTITY_PROVIDER", "trusted_headers")
    monkeypatch.setenv("IDENTITY_SIGNING_SECRET", "stage17-test-identity-secret-value")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "stage17-test-llm-key")

    settings = get_settings()

    assert settings.app_env == "production"
    assert settings.persistence_auto_create_schema is False
    assert settings.effective_persistence_database_url.endswith("/copilot")


def test_production_requires_checkpoint_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PERSISTENCE_DATABASE_URL", "postgresql+psycopg://user:secret@db/copilot")
    monkeypatch.setenv("PERSISTENCE_AUTO_CREATE_SCHEMA", "false")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://readonly:secret@business/quality")
    monkeypatch.setenv("DATABASE_PROVIDER", "sqlalchemy")
    monkeypatch.setenv("KNOWLEDGE_PROVIDER", "http")
    monkeypatch.setenv("RAG_BASE_URL", "https://rag.internal.example")
    monkeypatch.setenv("CHECKPOINT_ENABLED", "false")

    with pytest.raises(ConfigurationError, match="SETTINGS"):
        get_settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WORKFLOW_ENGINE", "fixed"),
        ("MAX_REPLAN_COUNT", "3"),
        ("MAX_TOTAL_EXECUTION_SECONDS", "0"),
        ("GRAPH_RECURSION_LIMIT", "10"),
        ("CHECKPOINT_CLEANUP_POLICY", "delete"),
    ],
)
def test_invalid_workflow_graph_configuration_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigurationError, match=name):
        get_settings()
