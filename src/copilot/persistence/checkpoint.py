"""Explicit LangGraph checkpoint migration and validation helpers."""

from __future__ import annotations

from sqlalchemy import Engine, inspect

from copilot.persistence.database import PersistenceSchemaError

POSTGRES_CHECKPOINT_TABLES = frozenset(
    {"checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"}
)


def require_postgres_checkpoint_schema(engine: Engine) -> None:
    """Fail API startup unless the explicit checkpoint migration step ran."""
    present = set(inspect(engine).get_table_names())
    if not POSTGRES_CHECKPOINT_TABLES.issubset(present):
        raise PersistenceSchemaError("LangGraph PostgreSQL checkpoint migration is required")


def migrate_postgres_checkpoints(database_url: str) -> None:
    """Run the official saver migrations from a single deployment init process."""
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(database_url) as saver:
        saver.setup()


__all__ = [
    "POSTGRES_CHECKPOINT_TABLES",
    "migrate_postgres_checkpoints",
    "require_postgres_checkpoint_schema",
]
