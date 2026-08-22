"""Create and profile a reviewed synthetic enterprise business dataset."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from copilot.config import PROJECT_ROOT, get_settings
from copilot.tools.database.ap_seed import (
    AP_DATASET_NAME,
    DEFAULT_AP_RANDOM_SEED,
    seed_accounts_payable_demo_database,
)
from copilot.tools.database.seed import DEFAULT_RANDOM_SEED, seed_demo_database

SUPPLIER_QUALITY_DATASET_NAME = "supplier-quality-v1"
DEFAULT_QUALITY_PROFILE_PATH = (
    PROJECT_ROOT / "data" / "demo" / "supplier_quality_dataset_profile.json"
)
DEFAULT_AP_PROFILE_PATH = PROJECT_ROOT / "data" / "demo" / "accounts_payable_dataset_profile.json"


def main(argv: Sequence[str] | None = None) -> int:
    """Initialize only the registered business tables and print a safe summary."""
    arguments = _parser().parse_args(argv)
    database_url = arguments.database_url or get_settings().database_url
    if arguments.dataset == AP_DATASET_NAME:
        seed = DEFAULT_AP_RANDOM_SEED if arguments.seed is None else arguments.seed
        ap_report = seed_accounts_payable_demo_database(
            database_url,
            base_directory=PROJECT_ROOT,
            random_seed=seed,
            reset=arguments.reset,
        )
        profile_path = (arguments.profile_output or DEFAULT_AP_PROFILE_PATH).resolve()
        summary = (
            "Accounts Payable demo database seeded: "
            f"database={ap_report.database_name}, "
            f"legal_entities={ap_report.legal_entity_count}, "
            f"business_units={ap_report.business_unit_count}, "
            f"purchase_orders={ap_report.purchase_order_count}, "
            f"invoices={ap_report.invoice_count}, "
            f"payments={ap_report.payment_count}, "
            f"checksum={ap_report.dataset_checksum}"
        )
        profile_data = ap_report.profile.as_dict()
    else:
        seed = DEFAULT_RANDOM_SEED if arguments.seed is None else arguments.seed
        quality_report = seed_demo_database(
            database_url,
            base_directory=PROJECT_ROOT,
            random_seed=seed,
            reset=arguments.reset,
        )
        profile_path = (arguments.profile_output or DEFAULT_QUALITY_PROFILE_PATH).resolve()
        period = f"{quality_report.start_date.isoformat()}..{quality_report.end_date.isoformat()}"
        summary = (
            "Supplier Quality demo database seeded: "
            f"database={quality_report.database_name}, "
            f"suppliers={quality_report.supplier_count}, "
            f"tenants={quality_report.tenant_count}, "
            f"inspections={quality_report.inspection_count}, "
            f"period={period}, "
            f"months={quality_report.months_covered}, "
            f"checksum={quality_report.dataset_checksum}"
        )
        profile_data = quality_report.profile.as_dict()
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(
        json.dumps(profile_data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"{summary}, profile={profile_path}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Seed one reviewed development/test business dataset. This utility is never called "
            "implicitly by application startup."
        )
    )
    parser.add_argument(
        "--dataset",
        choices=(SUPPLIER_QUALITY_DATASET_NAME, AP_DATASET_NAME),
        default=SUPPLIER_QUALITY_DATASET_NAME,
        help=f"Dataset to seed (default: {SUPPLIER_QUALITY_DATASET_NAME}).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Atomically replace existing demo rows instead of refusing a populated database.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help=(
            f"Override the dataset seed (Quality: {DEFAULT_RANDOM_SEED}; "
            f"AP: {DEFAULT_AP_RANDOM_SEED})."
        ),
    )
    parser.add_argument(
        "--database-url",
        help="Override DATABASE_URL for this explicit development/test seed operation.",
    )
    parser.add_argument(
        "--profile-output",
        type=Path,
        default=None,
        help="Dataset profile JSON path (defaults to the selected dataset's reviewed path).",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
