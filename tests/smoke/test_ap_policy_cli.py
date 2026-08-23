"""Smoke coverage for the Stage 3 controlled policy publication command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from copilot.bootstrap.policy_cli import policy_publish_main

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_ROOT = PROJECT_ROOT / "data" / "policies" / "accounts_payable" / "v1"


def test_policy_cli_validates_and_publishes_snapshot(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        policy_publish_main(
            [
                "--bundle-dir",
                str(BUNDLE_ROOT),
                "--output-dir",
                str(tmp_path),
                "--tenant-id",
                "TENANT-DEMO",
                "--index-revision",
                "smoke-1",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["validated"] is True
    assert output["published"] is True
    assert output["binding_count"] == 5
    assert Path(output["snapshot_location"]).is_dir()
