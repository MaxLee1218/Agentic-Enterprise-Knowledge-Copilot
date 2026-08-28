"""Validated application configuration loaded from environment variables."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import (
    AnyHttpUrl,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

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

    app_env: Literal["development", "test", "production"] = "development"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "text"] = "json"
    observability_enabled: bool = True
    metrics_enabled: bool = True
    trace_enabled: bool = True
    metrics_window_size: int = Field(default=1000, ge=1, le=100_000)
    max_trace_spans: int = Field(default=10_000, ge=100, le=1_000_000)
    max_trace_attributes: int = Field(default=32, ge=1, le=128)
    max_trace_attribute_length: int = Field(default=256, ge=16, le=4096)
    max_log_summary_length: int = Field(default=512, ge=64, le=8192)
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
    ap_policy_bundle_dir: Path = Path("data/policies/accounts_payable/v1")
    policy_snapshot_dir: Path = Path("data/policy-snapshots")
    knowledge_provider: Literal["mock", "http"] = "mock"
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
    # Enterprise business data is a separate, read-only Tool boundary.
    database_url: str
    database_provider: Literal["mock", "sqlalchemy"] = "mock"
    database_statement_timeout_seconds: float = Field(default=8, gt=0, le=8)
    max_database_rows: int = Field(default=50_000, ge=1, le=50_000)
    # Copilot-owned Task/Evidence/Audit persistence.  When omitted outside production, the
    # existing checkpoint SQLite path remains the backward-compatible local database.
    persistence_database_url: str | None = None
    persistence_auto_create_schema: bool = True
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    db_pool_timeout_seconds: float = Field(default=30, gt=0, le=300)
    db_pool_recycle_seconds: int = Field(default=1800, ge=30, le=86_400)
    db_connect_max_attempts: int = Field(default=5, ge=1, le=20)
    db_connect_retry_delay_seconds: float = Field(default=1, ge=0, le=30)
    queue_provider: Literal["postgresql"] = "postgresql"
    task_queue_visibility_timeout_seconds: int = Field(default=90, ge=60, le=3600)
    task_queue_max_queued_per_tenant: int = Field(default=1000, ge=1, le=1_000_000)
    task_queue_max_queued_global: int = Field(default=10_000, ge=1, le=10_000_000)
    task_queue_capacity_retry_after_seconds: int = Field(default=5, ge=1, le=3600)
    dispatcher_batch_size: int = Field(default=100, ge=1, le=1000)
    recovery_batch_size: int = Field(default=100, ge=1, le=1000)
    worker_concurrency: int = Field(default=4, ge=1, le=64)
    worker_poll_interval_seconds: float = Field(default=0.5, gt=0, le=30)
    worker_shutdown_grace_seconds: int = Field(default=30, ge=1, le=3600)
    worker_deployment_id: str = Field(default="local", min_length=1, max_length=200)
    execution_heartbeat_interval_seconds: int = Field(default=15, ge=1, le=300)
    execution_lease_ttl_seconds: int = Field(default=60, ge=5, le=900)
    max_runtime_recovery_attempts: int = Field(default=3, ge=1, le=10)
    artifact_dir: Path = Path("data/artifacts")
    report_max_size_bytes: int = Field(default=25 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    max_evidence_items: int = Field(default=500, ge=1, le=10_000)
    max_step_duration_seconds: int = Field(default=60, ge=1, le=300)
    max_task_steps: int = Field(default=14, ge=1, le=100)
    max_task_text_length: int = Field(default=10_000, ge=1, le=100_000)
    max_task_metadata_bytes: int = Field(default=16_384, ge=2, le=1_048_576)
    max_task_metadata_depth: int = Field(default=5, ge=1, le=20)
    max_task_metadata_items: int = Field(default=100, ge=0, le=10_000)
    task_force_read_only: bool = True
    task_require_approval_by_default: bool = False
    identity_provider: Literal["demo", "trusted_headers"] = "demo"
    identity_signing_secret: SecretStr | None = None
    identity_assertion_max_age_seconds: int = Field(default=60, ge=5, le=300)
    demo_user_id: str = Field(default="U-DEMO", min_length=1, max_length=200)
    demo_tenant_id: str = Field(default="TENANT-DEMO", min_length=1, max_length=200)
    demo_approval_roles: tuple[str, ...] = ("quality_analyst",)
    demo_identity_profile: Literal["supplier_quality", "local_enterprise"] = "supplier_quality"
    ap_policy_require_published_snapshot: bool = False
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
    # Stage 18 MCP interoperability is opt-in and pinned to one reviewed revision.
    mcp_enabled: bool = False
    mcp_client_enabled: bool = False
    mcp_server_enabled: bool = False
    mcp_protocol_revision: Literal["2025-11-25"] = "2025-11-25"
    mcp_http_host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    mcp_http_port: int = Field(default=8765, ge=1, le=65535)
    mcp_http_path: str = Field(default="/mcp", min_length=2, max_length=100)
    mcp_allow_public_bind: bool = False
    mcp_allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    mcp_allowed_origins: tuple[str, ...] = ()
    mcp_export_allowlist: tuple[str, ...] = ()
    mcp_connect_timeout_seconds: float = Field(default=10, gt=0, le=60)
    mcp_initialize_timeout_seconds: float = Field(default=15, gt=0, le=60)
    mcp_invocation_timeout_seconds: float = Field(default=60, gt=0, le=300)
    mcp_sampling_enabled: bool = False
    mcp_elicitation_enabled: bool = False
    mcp_jwt_issuer: str | None = Field(default=None, max_length=512)
    mcp_jwt_audience: str | None = Field(default=None, max_length=512)
    mcp_jwt_signing_key: SecretStr | None = None
    mcp_env_credential_names: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_environment_profile(self) -> Settings:
        """Fail fast on unsafe production-only combinations."""
        if (self.mcp_client_enabled or self.mcp_server_enabled) and not self.mcp_enabled:
            raise ValueError("MCP_ENABLED must be true when an MCP role is enabled")
        if self.mcp_http_host in {"0.0.0.0", "::"} and not self.mcp_allow_public_bind:
            raise ValueError("Public MCP bind requires MCP_ALLOW_PUBLIC_BIND=true")
        if not self.mcp_http_path.startswith("/") or "//" in self.mcp_http_path:
            raise ValueError("MCP_HTTP_PATH must be a canonical absolute path")
        if self.task_queue_max_queued_global < self.task_queue_max_queued_per_tenant:
            raise ValueError("Global Queue capacity must be at least the per-tenant capacity")
        if self.execution_lease_ttl_seconds < self.execution_heartbeat_interval_seconds * 3:
            raise ValueError("Execution lease TTL must allow at least three heartbeat intervals")
        if self.task_queue_visibility_timeout_seconds < self.execution_lease_ttl_seconds:
            raise ValueError("Queue visibility timeout must not be shorter than the lease TTL")
        if self.mcp_server_enabled and (
            self.mcp_jwt_issuer is None
            or self.mcp_jwt_audience is None
            or self.mcp_jwt_signing_key is None
            or len(self.mcp_jwt_signing_key.get_secret_value().encode("utf-8")) < 32
        ):
            raise ValueError("MCP Server requires issuer, audience, and a strong JWT key")
        if self.app_env != "production":
            return self
        if self.debug:
            raise ValueError("DEBUG must be disabled in production")
        if self.identity_provider != "trusted_headers":
            raise ValueError("IDENTITY_PROVIDER must be trusted_headers in production")
        if (
            self.identity_signing_secret is None
            or len(self.identity_signing_secret.get_secret_value().encode("utf-8")) < 32
        ):
            raise ValueError("IDENTITY_SIGNING_SECRET with at least 32 bytes is required")
        if self.persistence_database_url is None:
            raise ValueError("PERSISTENCE_DATABASE_URL is required in production")
        if make_url(self.persistence_database_url).get_backend_name() != "postgresql":
            raise ValueError("PERSISTENCE_DATABASE_URL must use PostgreSQL in production")
        if self.persistence_auto_create_schema:
            raise ValueError("PERSISTENCE_AUTO_CREATE_SCHEMA must be false in production")
        if not self.checkpoint_enabled:
            raise ValueError("CHECKPOINT_ENABLED must be true in production")
        if make_url(self.database_url).get_backend_name() == "sqlite":
            raise ValueError("DATABASE_URL must not use the demo SQLite database in production")
        if self.knowledge_provider != "http":
            raise ValueError("KNOWLEDGE_PROVIDER must be http in production")
        if not self.ap_policy_require_published_snapshot:
            raise ValueError("AP_POLICY_REQUIRE_PUBLISHED_SNAPSHOT must be true in production")
        if (
            self.ap_policy_bundle_dir
            == (PROJECT_ROOT / "data/policies/accounts_payable/v1").resolve()
        ):
            raise ValueError("AP_POLICY_BUNDLE_DIR must not use the demo bundle in production")
        if self.policy_snapshot_dir == (PROJECT_ROOT / "data/policy-snapshots").resolve():
            raise ValueError("POLICY_SNAPSHOT_DIR must be deployment-owned in production")
        if self.database_provider != "sqlalchemy":
            raise ValueError("DATABASE_PROVIDER must be sqlalchemy in production")
        if self.llm_provider != "deepseek":
            raise ValueError("LLM_PROVIDER must not use a mock provider in production")
        if self.llm_api_key is None or not self.llm_api_key.get_secret_value().strip():
            raise ValueError("LLM_API_KEY is required in production")
        rag_host = self.rag_base_url.host
        if rag_host in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("RAG_BASE_URL must not use a loopback address in production")
        return self

    @field_validator(
        "ap_policy_bundle_dir",
        "artifact_dir",
        "checkpoint_database_path",
        "policy_snapshot_dir",
        mode="after",
    )
    @classmethod
    def normalize_project_path(cls, value: Path) -> Path:
        """Resolve configured local persistence paths against the repository root."""
        return value.resolve() if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    @field_validator("persistence_database_url", mode="before")
    @classmethod
    def normalize_optional_database_url(cls, value: object) -> object:
        """Treat a blank optional deployment URL as the local SQLite fallback."""
        return None if value == "" else value

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

    def require_identity_signing_secret(self) -> SecretStr:
        """Return the configured trusted-gateway signing secret without fallback."""
        if self.identity_provider != "trusted_headers":
            raise ConfigurationError(
                "IDENTITY_SIGNING_SECRET is only used by IDENTITY_PROVIDER=trusted_headers"
            )
        if (
            self.identity_signing_secret is None
            or len(self.identity_signing_secret.get_secret_value().encode("utf-8")) < 32
        ):
            raise ConfigurationError("Missing required configuration: IDENTITY_SIGNING_SECRET")
        return self.identity_signing_secret

    @property
    def artifact_path(self) -> Path:
        """Return the normalized absolute directory for generated artifacts."""
        return self.artifact_dir

    @property
    def effective_persistence_database_url(self) -> str:
        """Return explicit deployment storage or the local SQLite compatibility path."""
        if self.persistence_database_url is not None:
            return self.persistence_database_url
        return f"sqlite:///{self.checkpoint_database_path}"


def _configuration_error(error: ValidationError) -> ConfigurationError:
    """Translate Pydantic validation details into a stable application exception."""
    missing = [str(item["loc"][0]).upper() for item in error.errors() if item["type"] == "missing"]
    if missing:
        return ConfigurationError(f"Missing required configuration: {', '.join(missing)}")

    invalid = [
        str(item["loc"][0]).upper() if item["loc"] else "SETTINGS" for item in error.errors()
    ]
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
