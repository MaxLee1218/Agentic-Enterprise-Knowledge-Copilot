"""Fail-closed AP policy inputs in the production Compose contract."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.production.yml"


def _render_compose(tmp_path: Path) -> dict[str, Any]:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")
    policy_bundle = tmp_path / "policy-bundle"
    policy_snapshots = tmp_path / "policy-snapshots"
    policy_bundle.mkdir()
    policy_snapshots.mkdir()
    environment = {
        **os.environ,
        "COPILOT_IMAGE": "enterprise-copilot:test",
        "FRONTEND_IMAGE": "enterprise-copilot-frontend:test",
        "RAG_IMAGE": "enterprise-rag-engine:test",
        "POSTGRES_DB": "copilot",
        "POSTGRES_USER": "copilot",
        "POSTGRES_PASSWORD": "test-only-password",
        "PERSISTENCE_DATABASE_URL": (
            "postgresql+psycopg://copilot:test-only-password@postgres/copilot"
        ),
        "DATABASE_URL": ("postgresql+psycopg://readonly:test-only-password@business-db/enterprise"),
        "LLM_API_KEY": "test-only-model-key",
        "IDENTITY_SIGNING_SECRET": "test-only-identity-signing-secret-32-bytes",
        "AP_POLICY_BUNDLE_PATH": str(policy_bundle),
        "AP_POLICY_SNAPSHOT_PATH": str(policy_snapshots),
    }
    result = subprocess.run(
        ("docker", "compose", "-f", str(COMPOSE_FILE), "config", "--format", "json"),
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail("Production Compose config did not render")
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def test_production_api_requires_read_only_published_ap_policy_inputs(tmp_path: Path) -> None:
    payload = _render_compose(tmp_path)
    api = payload["services"]["copilot-api"]
    environment = api["environment"]

    assert environment["AP_POLICY_REQUIRE_PUBLISHED_SNAPSHOT"] == "true"
    assert environment["AP_POLICY_BUNDLE_DIR"] == "/app/config/accounts-payable-policy"
    assert environment["POLICY_SNAPSHOT_DIR"] == "/app/data/policy-snapshots"

    mounts = {mount["target"]: mount for mount in api["volumes"]}
    for target in (
        "/app/config/accounts-payable-policy",
        "/app/data/policy-snapshots",
    ):
        assert mounts[target]["type"] == "bind"
        assert mounts[target]["read_only"] is True


def test_production_does_not_publish_policy_during_api_startup(tmp_path: Path) -> None:
    payload = _render_compose(tmp_path)

    assert "ap-policy-publish" not in payload["services"]
