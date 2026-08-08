"""Managed SQLAlchemy engine and transaction boundary for Copilot persistence."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import sleep
from typing import Any

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from copilot.persistence.models import PersistenceBase

LOGGER = logging.getLogger(__name__)


class PersistenceError(RuntimeError):
    """Stable infrastructure failure that never includes connection credentials."""


class PersistenceUnavailableError(PersistenceError):
    """Raised when the configured persistence service cannot be reached."""


class PersistenceSchemaError(PersistenceError):
    """Raised when the required Alembic-managed schema is absent."""


class PersistenceDatabase:
    """Own one pooled engine and provide rollback-safe sessions."""

    def __init__(
        self,
        database_url: str,
        *,
        base_directory: Path | None = None,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout_seconds: float = 30,
        pool_recycle_seconds: int = 1800,
    ) -> None:
        url = _normalize_url(make_url(database_url), base_directory)
        backend = url.get_backend_name()
        if backend not in {"sqlite", "postgresql"}:
            raise PersistenceError("Copilot persistence supports SQLite or PostgreSQL")
        engine_kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
        if backend == "sqlite":
            engine_kwargs["connect_args"] = {"check_same_thread": False}
            if url.database in (None, "", ":memory:"):
                engine_kwargs["poolclass"] = StaticPool
        else:
            engine_kwargs.update(
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=pool_timeout_seconds,
                pool_recycle=pool_recycle_seconds,
            )
        self._url = url
        self._engine = create_engine(url, **engine_kwargs)
        if backend == "sqlite":
            event.listen(self._engine, "connect", _enable_sqlite_foreign_keys)
        self._sessions = sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            autoflush=False,
        )

    @property
    def engine(self) -> Engine:
        """Return the owned engine for Alembic/test infrastructure only."""
        return self._engine

    @property
    def backend(self) -> str:
        """Return the credential-free SQLAlchemy backend name."""
        return self._url.get_backend_name()

    @property
    def url(self) -> URL:
        """Return SQLAlchemy's password-redacting URL value."""
        return self._url

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Commit one repository unit of work and rollback on every failure."""
        session = self._sessions()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def connect_with_retry(self, *, max_attempts: int, retry_delay_seconds: float) -> None:
        """Bound startup connectivity retries without leaking a URL or driver message."""
        for attempt in range(1, max_attempts + 1):
            try:
                self.ping()
                return
            except PersistenceUnavailableError:
                LOGGER.warning(
                    "Persistence connection attempt failed",
                    extra={
                        "event": "persistence_connection_failed",
                        "attempt": attempt,
                        "retry_count": attempt - 1,
                        "status": "RETRYING" if attempt < max_attempts else "FAILED",
                        "error_type": "PersistenceUnavailableError",
                    },
                )
                if attempt < max_attempts and retry_delay_seconds:
                    sleep(retry_delay_seconds)
        raise PersistenceUnavailableError("Copilot persistence is unavailable")

    def ping(self) -> None:
        """Verify that a connection can execute a minimal driver-neutral statement."""
        try:
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise PersistenceUnavailableError("Copilot persistence is unavailable") from exc

    def create_schema_for_tests(self) -> None:
        """Create current metadata only for isolated development and test databases."""
        PersistenceBase.metadata.create_all(self._engine)

    def require_schema(self) -> None:
        """Fail startup when Alembic has not created every authoritative table."""
        expected = set(PersistenceBase.metadata.tables)
        try:
            present = set(inspect(self._engine).get_table_names())
        except SQLAlchemyError as exc:
            raise PersistenceUnavailableError("Copilot persistence is unavailable") from exc
        missing = sorted(expected - present)
        if missing:
            raise PersistenceSchemaError("Copilot persistence migration is required")

    def dispose(self) -> None:
        """Close pooled connections owned by this process."""
        self._engine.dispose()

    @classmethod
    def from_sqlite_path(cls, path: Path) -> PersistenceDatabase:
        """Build a compatibility database for existing path-based repository tests."""
        absolute = path.resolve()
        absolute.parent.mkdir(parents=True, exist_ok=True)
        return cls(f"sqlite:///{absolute}")


def coerce_database(
    value: PersistenceDatabase | Path | None,
    *,
    initialize_schema: bool,
) -> tuple[PersistenceDatabase | None, bool]:
    """Normalize old path constructors to the shared database abstraction."""
    if value is None:
        return None, False
    database = (
        value
        if isinstance(value, PersistenceDatabase)
        else PersistenceDatabase.from_sqlite_path(value)
    )
    if initialize_schema:
        database.create_schema_for_tests()
    else:
        database.require_schema()
    return database, not isinstance(value, PersistenceDatabase)


def _normalize_url(url: URL, base_directory: Path | None) -> URL:
    if url.get_backend_name() != "sqlite" or url.database in (None, "", ":memory:"):
        return url
    assert url.database is not None
    path = Path(url.database).expanduser()
    if not path.is_absolute():
        path = (base_directory or Path.cwd()) / path
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return url.set(database=str(path))


def _enable_sqlite_foreign_keys(
    dbapi_connection: sqlite3.Connection,
    _connection_record: object,
) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
    finally:
        cursor.close()


__all__ = [
    "PersistenceDatabase",
    "PersistenceError",
    "PersistenceSchemaError",
    "PersistenceUnavailableError",
    "coerce_database",
]
