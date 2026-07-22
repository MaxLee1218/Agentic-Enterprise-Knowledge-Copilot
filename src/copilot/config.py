"""Validated application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigurationError(RuntimeError):
    """Raised when application configuration is missing or invalid."""


class Settings(BaseSettings):
    """Application settings sourced from process environment and a local ``.env`` file."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    rag_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8000")
    rag_timeout_seconds: int = Field(default=30, gt=0)
    database_url: str
    artifact_dir: Path = Path("data/artifacts")
    max_task_steps: int = Field(default=10, gt=0)
    workflow_max_retries: int = Field(default=2, ge=0, le=2)
    workflow_retry_delay_seconds: float = Field(default=0, ge=0)

    @field_validator("artifact_dir", mode="after")
    @classmethod
    def normalize_artifact_dir(cls, value: Path) -> Path:
        """Resolve a relative artifact directory against the repository root."""
        return value.resolve() if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    @property
    def artifact_path(self) -> Path:
        """Return the normalized absolute directory for generated artifacts."""
        return self.artifact_dir


def _configuration_error(error: ValidationError) -> ConfigurationError:
    """Translate Pydantic validation details into a stable application exception."""
    missing = [str(item["loc"][0]).upper() for item in error.errors() if item["type"] == "missing"]
    if missing:
        return ConfigurationError(f"Missing required configuration: {', '.join(missing)}")

    invalid = [str(item["loc"][0]).upper() for item in error.errors()]
    fields = ", ".join(dict.fromkeys(invalid))
    return ConfigurationError(f"Invalid configuration: {fields}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide validated settings instance.

    Raises:
        ConfigurationError: If required configuration is missing or a value is invalid.
    """
    try:
        # Pydantic Settings supplies required values from configured environment sources.
        return Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        raise _configuration_error(error) from error
