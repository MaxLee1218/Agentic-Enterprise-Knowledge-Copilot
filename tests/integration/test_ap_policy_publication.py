"""Atomic publication and immutable snapshot retention for AP policy Stage 3."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from copilot.tools.knowledge import load_ap_policy_bundle, publish_ap_policy_bundle

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_ROOT = PROJECT_ROOT / "data" / "policies" / "accounts_payable" / "v1"


def test_atomic_publication_writes_bound_payload_rule_manifest_and_current_pointer(
    tmp_path: Path,
) -> None:
    bundle = load_ap_policy_bundle(BUNDLE_ROOT)
    published_at = datetime(2026, 8, 23, 0, 0, tzinfo=UTC)

    snapshot = publish_ap_policy_bundle(
        bundle,
        tmp_path,
        index_revision="generation-1",
        published_at=published_at,
    )
    snapshot_directory = tmp_path / "TENANT-DEMO" / snapshot.snapshot_id
    current = json.loads((tmp_path / "TENANT-DEMO" / "current.json").read_text())
    lines = (snapshot_directory / "documents.jsonl").read_text().splitlines()

    assert snapshot_directory.is_dir()
    assert len(lines) == 8
    assert all(json.loads(line)["metadata"]["tenant_id"] == "TENANT-DEMO" for line in lines)
    assert (snapshot_directory / "ap_rules.2026.1.json").is_file()
    assert current == {
        "publication_checksum": snapshot.publication_checksum,
        "schema_version": "ap-policy-current.v1",
        "snapshot_id": snapshot.snapshot_id,
    }
    assert not tuple((tmp_path / "TENANT-DEMO").glob(".policy-staging-*"))


def test_reindex_keeps_old_snapshot_and_idempotent_republish_reuses_it(tmp_path: Path) -> None:
    bundle = load_ap_policy_bundle(BUNDLE_ROOT)
    first = publish_ap_policy_bundle(
        bundle,
        tmp_path,
        index_revision="generation-1",
        published_at=datetime(2026, 8, 23, 0, 0, tzinfo=UTC),
    )
    replay = publish_ap_policy_bundle(
        bundle,
        tmp_path,
        index_revision="generation-1",
        published_at=datetime(2026, 8, 23, 1, 0, tzinfo=UTC),
    )
    second = publish_ap_policy_bundle(
        bundle,
        tmp_path,
        index_revision="generation-2",
        published_at=datetime(2026, 8, 23, 2, 0, tzinfo=UTC),
    )

    assert replay == first
    assert first.snapshot_id != second.snapshot_id
    tenant_root = tmp_path / "TENANT-DEMO"
    assert (tenant_root / first.snapshot_id / "snapshot.json").is_file()
    assert (tenant_root / second.snapshot_id / "snapshot.json").is_file()
    current = json.loads((tenant_root / "current.json").read_text())
    assert current["snapshot_id"] == second.snapshot_id
