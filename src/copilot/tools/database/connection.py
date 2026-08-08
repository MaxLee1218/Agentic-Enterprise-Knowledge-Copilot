"""Managed SQLAlchemy engine, sessions, and bounded read-only execution."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from sqlalchemy import Engine, Select, create_engine, event, func, inspect, select
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
        self._backend_name = url.get_backend_name()
        if self._backend_name not in {"sqlite", "postgresql"}:
            raise DatabaseConfigurationError(
                "Database Tool supports approved SQLite and PostgreSQL connections only"
            )
        self._database_path = (
            _database_path(url.database, base_directory) if self._backend_name == "sqlite" else None
        )
        normalized_url = (
            url.set(database=str(self._database_path))
            if self._backend_name == "sqlite" and self._database_path is not None
            else url
        )
        engine_kwargs: dict[str, Any] = {
            "future": True,
            "pool_pre_ping": True,
        }
        if self._backend_name == "sqlite":
            engine_kwargs["connect_args"] = {"check_same_thread": False}
        if self._backend_name == "sqlite" and self._database_path is None:
            engine_kwargs["poolclass"] = StaticPool
        self._engine = create_engine(normalized_url, **engine_kwargs)
        self._read_only = read_only
        self._session_factory = sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            autoflush=False,
        )
        if self._backend_name == "sqlite":
            event.listen(self._engine, "connect", self._configure_sqlite_connection)

    @property
    def engine(self) -> Engine:
        """Expose the owned engine for schema initialization inside this adapter."""
        return self._engine

    @property
    def database_name(self) -> str:
        """Return a safe database identity without connection credentials or paths."""
        if self._database_path is not None:
            return self._database_path.name
        database = make_url(str(self._engine.url)).database
        return database or ":memory:"

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
        """Execute one validated SELECT with a driver-enforced read-only deadline."""
        self._ensure_readable_database()
        try:
            with self._engine.connect() as connection:
                if self._backend_name == "postgresql":
                    rows = self._execute_postgresql_read_only(
                        connection,
                        statement,
                        parameters,
                        timeout_seconds,
                    )
                else:
                    rows = self._execute_sqlite_with_deadline(
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

    def check_ready(self, table_names: tuple[str, ...]) -> bool:
        """Verify connectivity and the approved schema without returning database details."""
        try:
            self.require_tables(table_names)
        except (DatabaseConnectionError, DatabaseSchemaNotFoundError):
            return False
        return True

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
    def _execute_sqlite_with_deadline(
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

    def _execute_postgresql_read_only(
        self,
        connection: Connection,
        statement: Select[Any],
        parameters: Mapping[str, Any],
        timeout_seconds: float,
    ) -> tuple[Mapping[str, Any], ...]:
        """Execute one statement in a server-enforced read-only, time-bounded transaction."""
        timeout_milliseconds = max(1, int(timeout_seconds * 1000))
        try:
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.execute(
                    select(
                        func.set_config(
                            "statement_timeout",
                            f"{timeout_milliseconds}ms",
                            True,
                        )
                    )
                )
                result = connection.execute(statement, dict(parameters))
                return tuple(dict(row) for row in result.mappings())
        except DBAPIError as exc:
            if _postgres_error_code(exc) == "57014":
                raise DatabaseStatementTimeoutError(
                    "Database statement exceeded its configured timeout"
                ) from exc
            raise

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


def _postgres_error_code(exc: DBAPIError) -> str | None:
    original = exc.orig
    return getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)


__all__ = ["DatabaseConnection", "DatabaseRows"]
