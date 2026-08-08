"""Alembic upgrade/current/downgrade integration coverage on isolated SQLite."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from copilot.config import PROJECT_ROOT, get_settings
from copilot.persistence.models import PersistenceBase

pytestmark = pytest.mark.integration


def test_fresh_database_upgrade_reaches_head_and_safe_downgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "alembic.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("PERSISTENCE_DATABASE_URL", database_url)
    get_settings.cache_clear()
    configuration = Config(str(PROJECT_ROOT / "alembic.ini"))

    command.upgrade(configuration, "head")

    engine = create_engine(database_url)
    try:
        assert set(PersistenceBase.metadata.tables).issubset(inspect(engine).get_table_names())
        with engine.connect() as connection:
            revision = MigrationContext.configure(connection).get_current_revision()
        assert revision == "20260807_0001"

        command.downgrade(configuration, "base")
        assert set(PersistenceBase.metadata.tables).isdisjoint(inspect(engine).get_table_names())
    finally:
        engine.dispose()
        get_settings.cache_clear()
