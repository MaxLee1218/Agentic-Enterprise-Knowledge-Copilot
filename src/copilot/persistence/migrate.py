"""Single-process deployment migration command."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from copilot.config import PROJECT_ROOT, get_settings
from copilot.persistence.checkpoint import migrate_postgres_checkpoints


def migrate() -> None:
    """Upgrade Copilot tables, then initialize vendor-owned checkpoint tables."""
    settings = get_settings()
    configuration = Config(str(_alembic_configuration_path()))
    command.upgrade(configuration, "head")
    if settings.effective_persistence_database_url.startswith("postgresql"):
        from sqlalchemy.engine import make_url

        url = make_url(settings.effective_persistence_database_url).set(drivername="postgresql")
        migrate_postgres_checkpoints(url.render_as_string(hide_password=False))


def _alembic_configuration_path() -> Path:
    """Find deployment configuration in the image workdir or source checkout."""
    candidates = (Path.cwd() / "alembic.ini", PROJECT_ROOT / "alembic.ini")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("alembic.ini is not available in the deployment image")


def main() -> int:
    migrate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
