"""Deployment entry point for explicit LangGraph PostgreSQL checkpoint migrations."""

from __future__ import annotations

from sqlalchemy.engine import make_url

from copilot.config import get_settings
from copilot.persistence.checkpoint import migrate_postgres_checkpoints


def main() -> int:
    """Initialize vendor-owned checkpoint tables outside API worker startup."""
    settings = get_settings()
    url = make_url(settings.effective_persistence_database_url)
    if url.get_backend_name() != "postgresql":
        return 0
    conninfo = url.set(drivername="postgresql").render_as_string(hide_password=False)
    migrate_postgres_checkpoints(conninfo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
