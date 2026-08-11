"""Create and profile the synthetic Supplier Quality enterprise business database."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from copilot.config import PROJECT_ROOT, get_settings
from copilot.tools.database.seed import DEFAULT_RANDOM_SEED, seed_demo_database

DEFAULT_PROFILE_PATH = PROJECT_ROOT / "data" / "demo" / "supplier_quality_dataset_profile.json"


def main(argv: Sequence[str] | None = None) -> int:
    """Initialize only the registered business tables and print a safe summary."""
    arguments = _parser().parse_args(argv)
    database_url = arguments.database_url or get_settings().database_url
    report = seed_demo_database(
        database_url,
        base_directory=PROJECT_ROOT,
        random_seed=arguments.seed,
        reset=arguments.reset,
    )
    profile_path = arguments.profile_output.resolve()
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(
        json.dumps(report.profile.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "Demo business database seeded: "
        f"database={report.database_name}, "
        f"suppliers={report.supplier_count}, "
        f"tenants={report.tenant_count}, "
        f"inspections={report.inspection_count}, "
        f"period={report.start_date.isoformat()}..{report.end_date.isoformat()}, "
        f"months={report.months_covered}, "
        f"checksum={report.dataset_checksum}, "
        f"profile={profile_path}"
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Seed the development/test Supplier Quality business database with deterministic "
            "synthetic enterprise data. This utility is never called by application startup."
        )
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Atomically replace existing demo rows instead of refusing a populated database.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_RANDOM_SEED,
        help=f"Deterministic generator seed (default: {DEFAULT_RANDOM_SEED}).",
    )
    parser.add_argument(
        "--database-url",
        help="Override DATABASE_URL for this explicit development/test seed operation.",
    )
    parser.add_argument(
        "--profile-output",
        type=Path,
        default=DEFAULT_PROFILE_PATH,
        help=f"Dataset profile JSON path (default: {DEFAULT_PROFILE_PATH}).",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
