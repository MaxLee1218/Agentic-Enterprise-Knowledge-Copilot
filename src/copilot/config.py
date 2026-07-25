"""Validated application configuration loaded from environment variables."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


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
    rag_timeout_seconds: float = Field(default=30, gt=0)
    rag_max_attempts: int = Field(default=3, ge=1, le=3)
    rag_retry_base_delay_seconds: float = Field(default=0.2, ge=0, le=10)
    rag_user_agent: str = Field(
        default="agentic-enterprise-knowledge-copilot/0.1.0",
        min_length=1,
        max_length=256,
    )
    rag_trace_header: str = Field(default="X-Trace-ID", min_length=1, max_length=128)
    database_url: str
    database_statement_timeout_seconds: float = Field(default=8, gt=0, le=8)
    artifact_dir: Path = Path("data/artifacts")
    max_task_steps: int = Field(default=10, gt=0)
    workflow_max_retries: int = Field(default=2, ge=0, le=2)
    workflow_retry_delay_seconds: float = Field(default=0, ge=0)

    @field_validator("artifact_dir", mode="after")
    @classmethod
    def normalize_artifact_dir(cls, value: Path) -> Path:
        """Resolve a relative artifact directory against the repository root."""
        return value.resolve() if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    @field_validator("rag_base_url", mode="after")
    @classmethod
    def normalize_rag_base_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        """Remove trailing slashes so endpoint paths are composed exactly once."""
        return AnyHttpUrl(str(value).rstrip("/"))

    @field_validator("rag_user_agent")
    @classmethod
    def validate_rag_user_agent(cls, value: str) -> str:
        """Reject blank or control-bearing configured User Agent values."""
        clean = value.strip()
        if not clean or any(ord(character) < 32 or ord(character) == 127 for character in clean):
            raise ValueError("RAG_USER_AGENT is invalid")
        return clean

    @field_validator("rag_trace_header")
    @classmethod
    def validate_rag_trace_header(cls, value: str) -> str:
        """Require an RFC-compatible HTTP header name."""
        clean = value.strip()
        if not _HTTP_HEADER_NAME.fullmatch(clean):
            raise ValueError("RAG_TRACE_HEADER is invalid")
        return clean

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
