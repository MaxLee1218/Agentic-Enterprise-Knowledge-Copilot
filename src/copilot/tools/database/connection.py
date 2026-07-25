"""Managed SQLAlchemy engine, sessions, and bounded read-only execution."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from sqlalchemy import Engine, Select, create_engine, event, inspect
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from copilot.tools.database.errors import (
    DatabaseConfigurationError,
    DatabaseConnectionError,
    DatabaseSchemaNotFoundError,
    DatabaseStatementTimeoutError,
)


@dataclass(frozen=True, slots=True)
class DatabaseRows:
    """Driver-neutral rows returned without exposing SQLAlchemy objects."""

    rows: tuple[Mapping[str, Any], ...]


class DatabaseConnection:
    """Own an engine and provide leak-free, read-only query execution."""

    def __init__(
        self,
        database_url: str,
        *,
        read_only: bool = True,
        base_directory: Path | None = None,
    ) -> None:
        url = make_url(database_url)
        if url.get_backend_name() != "sqlite":
            raise DatabaseConfigurationError("Demo Database Tool supports SQLite only")
        self._database_path = _database_path(url.database, base_directory)
        normalized_url = (
            url.set(database=str(self._database_path)) if self._database_path is not None else url
        )
        engine_kwargs: dict[str, Any] = {
            "future": True,
            "pool_pre_ping": True,
            "connect_args": {"check_same_thread": False},
        }
        if self._database_path is None:
            engine_kwargs["poolclass"] = StaticPool
        self._engine = create_engine(normalized_url, **engine_kwargs)
        self._read_only = read_only
        self._session_factory = sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            autoflush=False,
        )
        event.listen(self._engine, "connect", self._configure_sqlite_connection)

    @property
    def engine(self) -> Engine:
        """Expose the owned engine for schema initialization inside this adapter."""
        return self._engine

    @property
    def database_name(self) -> str:
        """Return a safe database identity without connection credentials or paths."""
        return self._database_path.name if self._database_path is not None else ":memory:"

    @property
    def database_path(self) -> Path | None:
        """Return the local SQLite path, or ``None`` for an in-memory database."""
        return self._database_path

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Yield a managed session and always rollback or close on failure."""
        session = self._session_factory()
        try:
            yield session
            if self._read_only:
                session.rollback()
            else:
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def execute_select(
        self,
        statement: Select[Any],
        parameters: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> DatabaseRows:
        """Execute one validated SELECT with a SQLite progress deadline."""
        self._ensure_readable_database()
        try:
            with self._engine.connect() as connection:
                rows = self._execute_with_deadline(
                    connection,
                    statement,
                    parameters,
                    timeout_seconds,
                )
        except DatabaseStatementTimeoutError:
            raise
        except (DBAPIError, SQLAlchemyError) as exc:
            raise DatabaseConnectionError("Database query could not be completed") from exc
        return DatabaseRows(rows=rows)

    def require_tables(self, table_names: tuple[str, ...]) -> None:
        """Fail distinctly when the registered schema has not been initialized."""
        self._ensure_readable_database()
        try:
            existing = frozenset(inspect(self._engine).get_table_names())
        except SQLAlchemyError as exc:
            raise DatabaseConnectionError("Database schema could not be inspected") from exc
        if not set(table_names).issubset(existing):
            raise DatabaseSchemaNotFoundError("Registered quality.v1 schema is unavailable")

    def dispose(self) -> None:
        """Release pooled connections owned by this adapter."""
        self._engine.dispose()

    def _configure_sqlite_connection(
        self,
        dbapi_connection: sqlite3.Connection,
        _connection_record: object,
    ) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            if self._read_only:
                cursor.execute("PRAGMA query_only = ON")
        finally:
            cursor.close()

    @staticmethod
    def _execute_with_deadline(
        connection: Connection,
        statement: Select[Any],
        parameters: Mapping[str, Any],
        timeout_seconds: float,
    ) -> tuple[Mapping[str, Any], ...]:
        raw_connection = connection.connection.driver_connection
        if not isinstance(raw_connection, sqlite3.Connection):
            raise DatabaseConfigurationError("Expected a SQLite driver connection")
        deadline = monotonic() + timeout_seconds
        timed_out = False

        def progress() -> int:
            nonlocal timed_out
            timed_out = monotonic() >= deadline
            return 1 if timed_out else 0

        raw_connection.set_progress_handler(progress, 1000)
        try:
            result = connection.execute(statement, dict(parameters))
            return tuple(dict(row) for row in result.mappings())
        except DBAPIError as exc:
            if timed_out:
                raise DatabaseStatementTimeoutError(
                    "Database statement exceeded its configured timeout"
                ) from exc
            raise
        finally:
            raw_connection.set_progress_handler(None, 0)

    def _ensure_readable_database(self) -> None:
        if self._database_path is not None and not self._database_path.is_file():
            raise DatabaseConnectionError("Configured demo database is unavailable")


def _database_path(database: str | None, base_directory: Path | None) -> Path | None:
    if database in (None, "", ":memory:"):
        return None
    assert database is not None
    path = Path(database).expanduser()
    if not path.is_absolute():
        path = (base_directory or Path.cwd()) / path
    return path.resolve()


__all__ = ["DatabaseConnection", "DatabaseRows"]
