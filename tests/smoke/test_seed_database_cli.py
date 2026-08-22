"""Seed CLI smoke coverage against an isolated SQLite business database."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.seed_demo_database import main


def test_seed_cli_resets_database_and_writes_profile(tmp_path: Path) -> None:
    database_path = tmp_path / "cli-business.db"
    profile_path = tmp_path / "dataset-profile.json"
    arguments = [
        "--reset",
        "--database-url",
        f"sqlite:///{database_path}",
        "--profile-output",
        str(profile_path),
    ]

    assert main(arguments) == 0
    assert main(arguments) == 0
    profile = json.loads(profile_path.read_text(encoding="utf-8"))

    assert database_path.is_file()
    assert profile["supplier_count"] == 17
    assert profile["inspection_record_count"] == 5000
    assert profile["months_covered"] == [f"2026-{month:02d}" for month in range(1, 13)]


def test_seed_cli_selects_accounts_payable_dataset(tmp_path: Path) -> None:
    database_path = tmp_path / "cli-ap-business.db"
    profile_path = tmp_path / "ap-dataset-profile.json"
    arguments = [
        "--dataset",
        "accounts-payable-v1",
        "--reset",
        "--database-url",
        f"sqlite:///{database_path}",
        "--profile-output",
        str(profile_path),
    ]

    assert main(arguments) == 0
    assert main(arguments) == 0
    profile = json.loads(profile_path.read_text(encoding="utf-8"))

    assert profile["dataset_name"] == "accounts-payable-v1"
    assert profile["profile_version"] == "ap-demo-dataset.v1"
    assert profile["random_seed"] == 42
    assert profile["referenced_supplier_count"] == 6
    assert profile["row_counts"] == {
        "business_units": 4,
        "invoices": 27,
        "legal_entities": 3,
        "payments": 11,
        "purchase_orders": 24,
    }
