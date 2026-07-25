"""Create and deterministically reseed the configured SQLite demo database."""

from __future__ import annotations

from copilot.config import PROJECT_ROOT, get_settings
from copilot.tools.database.seed import seed_demo_database


def main() -> int:
    """Initialize only the registered demo tables and print a safe summary."""
    settings = get_settings()
    report = seed_demo_database(settings.database_url, base_directory=PROJECT_ROOT)
    print(
        "Demo database seeded: "
        f"database={report.database_name}, "
        f"suppliers={report.supplier_count}, "
        f"deviations={report.deviation_count}, "
        f"inspections={report.inspection_count}, "
        f"corrective_actions={report.corrective_action_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
