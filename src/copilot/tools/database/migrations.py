"""Explicit migration API for the isolated enterprise business database."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from copilot.config import PROJECT_ROOT

BUSINESS_SCHEMA_BASELINE_REVISION = "20260811_b001"
BUSINESS_SCHEMA_HEAD_REVISION = "20260822_b002"


def business_migration_config(database_url: str) -> Config:
    """Build a credential-safe configuration for one explicit business database URL."""
    configuration = Config(str(_business_alembic_configuration_path()))
    configuration.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return configuration


def _business_alembic_configuration_path() -> Path:
    candidates = (
        Path.cwd() / "business_alembic.ini",
        PROJECT_ROOT / "business_alembic.ini",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("business_alembic.ini is not available in the deployment image")


def upgrade_business_schema(database_url: str, revision: str = "head") -> None:
    """Upgrade only the enterprise business schema; never touch Copilot persistence."""
    command.upgrade(business_migration_config(database_url), revision)


def downgrade_business_schema(database_url: str, revision: str) -> None:
    """Downgrade an explicitly isolated business database to a reviewed revision."""
    command.downgrade(business_migration_config(database_url), revision)


__all__ = [
    "BUSINESS_SCHEMA_BASELINE_REVISION",
    "BUSINESS_SCHEMA_HEAD_REVISION",
    "business_migration_config",
    "downgrade_business_schema",
    "upgrade_business_schema",
]
