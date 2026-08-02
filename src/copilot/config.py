"""Validated application configuration loaded from environment variables."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, ValidationError, field_validator
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
    llm_provider: Literal["mock", "deepseek"] = "mock"
    llm_model: str = Field(default="deepseek-chat", min_length=1, max_length=128)
    llm_base_url: AnyHttpUrl = AnyHttpUrl("https://api.deepseek.com")
    llm_api_key: SecretStr | None = None
    llm_connect_timeout_seconds: float = Field(default=10, gt=0, le=60)
    llm_read_timeout_seconds: float = Field(default=60, gt=0, le=300)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_retry_base_delay_seconds: float = Field(default=0.2, ge=0, le=10)
    llm_max_output_tokens: int = Field(default=4096, ge=1, le=65536)
    llm_temperature: float = Field(default=0, ge=0, le=2)
    llm_user_agent: str = Field(
        default="agentic-enterprise-knowledge-copilot/0.1.0",
        min_length=1,
        max_length=256,
    )
    llm_trace_header: str = Field(default="X-Trace-ID", min_length=1, max_length=128)
    max_plan_repair_attempts: int = Field(default=2, ge=0, le=2)
    database_url: str
    database_statement_timeout_seconds: float = Field(default=8, gt=0, le=8)
    artifact_dir: Path = Path("data/artifacts")
    report_max_size_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    max_task_steps: int = Field(default=10, gt=0)
    max_task_text_length: int = Field(default=10_000, ge=1, le=100_000)
    max_task_metadata_bytes: int = Field(default=16_384, ge=2, le=1_048_576)
    max_task_metadata_depth: int = Field(default=5, ge=1, le=20)
    max_task_metadata_items: int = Field(default=100, ge=0, le=10_000)
    task_force_read_only: bool = True
    task_require_approval_by_default: bool = False
    demo_user_id: str = Field(default="U-DEMO", min_length=1, max_length=200)
    demo_tenant_id: str = Field(default="TENANT-DEMO", min_length=1, max_length=200)
    demo_approval_roles: tuple[str, ...] = ("quality_data_approver",)
    approval_ttl_seconds: int = Field(default=86_400, ge=60, le=604_800)
    workflow_max_retries: int = Field(default=2, ge=0, le=2)
    workflow_retry_delay_seconds: float = Field(default=0, ge=0)
    workflow_engine: Literal["langgraph"] = "langgraph"
    checkpoint_enabled: bool = True
    checkpoint_database_path: Path = Path("data/database/workflow-checkpoints.db")
    checkpoint_connection_timeout_seconds: float = Field(default=5, gt=0, le=60)
    checkpoint_cleanup_policy: Literal["retain"] = "retain"
    max_replan_count: int = Field(default=2, ge=0, le=2)
    max_total_execution_seconds: int = Field(default=300, ge=1, le=3600)
    graph_recursion_limit: int = Field(default=100, ge=20, le=1000)

    @field_validator("artifact_dir", "checkpoint_database_path", mode="after")
    @classmethod
    def normalize_project_path(cls, value: Path) -> Path:
        """Resolve configured local persistence paths against the repository root."""
        return value.resolve() if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    @field_validator("rag_base_url", "llm_base_url", mode="after")
    @classmethod
    def normalize_base_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        """Remove trailing slashes so endpoint paths are composed exactly once."""
        return AnyHttpUrl(str(value).rstrip("/"))

    @field_validator("rag_user_agent", "llm_user_agent")
    @classmethod
    def validate_user_agent(cls, value: str) -> str:
        """Reject blank or control-bearing configured User Agent values."""
        clean = value.strip()
        if not clean or any(ord(character) < 32 or ord(character) == 127 for character in clean):
            raise ValueError("configured User Agent is invalid")
        return clean

    @field_validator("rag_trace_header", "llm_trace_header")
    @classmethod
    def validate_trace_header(cls, value: str) -> str:
        """Require an RFC-compatible HTTP header name."""
        clean = value.strip()
        if not _HTTP_HEADER_NAME.fullmatch(clean):
            raise ValueError("configured trace header is invalid")
        return clean

    def require_llm_api_key(self) -> SecretStr:
        """Return the configured secret only for the real DeepSeek provider."""
        if self.llm_provider != "deepseek":
            raise ConfigurationError("LLM_API_KEY is only required for LLM_PROVIDER=deepseek")
        if self.llm_api_key is None or not self.llm_api_key.get_secret_value().strip():
            raise ConfigurationError("Missing required configuration: LLM_API_KEY")
        return self.llm_api_key

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
