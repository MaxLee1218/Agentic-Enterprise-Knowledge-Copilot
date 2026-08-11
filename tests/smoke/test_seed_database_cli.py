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
