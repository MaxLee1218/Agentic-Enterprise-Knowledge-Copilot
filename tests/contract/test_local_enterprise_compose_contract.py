"""Static and Compose-rendered contracts for the Local Enterprise topology."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.local-enterprise.yml"
ENV_FILE = PROJECT_ROOT / ".env.local-enterprise.example"


def _render_compose(*, include_ingest: bool = False) -> dict[str, Any]:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")
    command = [
        "docker",
        "compose",
        "--env-file",
        str(ENV_FILE),
        "-f",
        str(COMPOSE_FILE),
    ]
    if include_ingest:
        command.extend(("--profile", "ingest"))
    command.extend(("config", "--format", "json"))
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail("Local Enterprise Compose config did not render")
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def test_local_enterprise_topology_has_only_one_browser_facing_port() -> None:
    payload = _render_compose()
    services = payload["services"]
    assert {
        "frontend",
        "copilot-api",
        "copilot-migrate",
        "copilot-postgres",
        "business-postgres",
        "enterprise-rag-engine",
        "rag-warmup",
        "rag-generation-stub",
    }.issubset(services)
    published = {
        service_name: service.get("ports", [])
        for service_name, service in services.items()
        if service.get("ports")
    }
    assert set(published) == {"frontend"}
    assert published["frontend"][0]["host_ip"] == "127.0.0.1"


def test_frontend_has_no_backend_credentials_or_dependency_network() -> None:
    payload = _render_compose()
    frontend = payload["services"]["frontend"]
    assert not frontend.get("environment")
    assert set(frontend["networks"]) == {"enterprise-edge"}
    assert "enterprise-backend" not in frontend["networks"]
    nginx = (PROJECT_ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    assert "proxy_pass http://copilot-api:8000/" in nginx
    for forbidden in (
        "business-postgres",
        "enterprise-rag-engine",
        "DATABASE_URL",
        "DEEPSEEK_API_KEY",
    ):
        assert forbidden not in nginx


def test_local_enterprise_uses_separate_state_volumes_and_compose_dns() -> None:
    payload = _render_compose()
    services = payload["services"]
    api_environment = services["copilot-api"]["environment"]
    assert "@copilot-postgres:5432/" in api_environment["PERSISTENCE_DATABASE_URL"]
    assert "@business-postgres:5432/" in api_environment["DATABASE_URL"]
    assert api_environment["RAG_BASE_URL"] == "http://enterprise-rag-engine:8000"
    volumes = set(payload["volumes"])
    assert {
        "copilot-postgres-data",
        "business-postgres-data",
        "copilot-artifacts",
        "enterprise-rag-data",
    }.issubset(volumes)


def test_formal_rag_is_the_default_with_read_only_documents_and_isolated_data() -> None:
    payload = _render_compose(include_ingest=True)
    services = payload["services"]
    rag = services["enterprise-rag-engine"]
    ingest = services["enterprise-rag-ingest"]
    assert rag["image"] == "enterprise-rag-engine:local"
    assert ingest["image"] == "enterprise-rag-engine:local"
    assert set(rag["networks"]) == {"enterprise-backend"}
    assert set(ingest["networks"]) == {"enterprise-backend"}

    rag_mounts = {mount["target"]: mount for mount in rag["volumes"]}
    ingest_mounts = {mount["target"]: mount for mount in ingest["volumes"]}
    for mounts in (rag_mounts, ingest_mounts):
        assert mounts["/app/data"]["type"] == "volume"
        assert mounts["/app/data"]["source"].endswith("enterprise-rag-data")
        assert mounts["/app/enterprise-documents"]["type"] == "bind"
        assert mounts["/app/enterprise-documents"]["read_only"] is True

    command = ingest["command"]
    assert command[command.index("--collection") + 1] == "supplier_quality_demo"
    assert command[command.index("--input") + 1] == "/app/enterprise-documents/pdf"
    assert "--reset" in command


def test_formal_rag_uses_real_reranking_and_safe_local_generation_by_default() -> None:
    payload = _render_compose()
    services = payload["services"]
    rag = services["enterprise-rag-engine"]
    rag_environment = rag["environment"]
    assert rag_environment["RERANKER_ENABLED"] == "true"
    assert rag_environment["DEEPSEEK_BASE_URL"] == "http://rag-generation-stub:8000"
    assert rag_environment["VECTOR_COLLECTION_NAME"] == "supplier_quality_demo"
    assert "rag-generation-stub" in rag_environment["NO_PROXY"]
    assert rag["depends_on"]["rag-generation-stub"]["condition"] == "service_healthy"
    assert set(services["rag-generation-stub"]["networks"]) == {"enterprise-backend"}
    assert not services["rag-generation-stub"].get("ports")
    warmup = services["rag-warmup"]
    assert warmup["depends_on"]["enterprise-rag-engine"]["condition"] == "service_healthy"
    assert warmup["restart"] == "no"
    assert set(warmup["networks"]) == {"enterprise-backend"}
    assert services["copilot-api"]["depends_on"]["rag-warmup"]["condition"] == (
        "service_completed_successfully"
    )


def test_compose_source_contains_no_host_specific_absolute_path_or_fixture_default() -> None:
    source = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "/Users/" not in source
    assert "stage17-rag-contract:validation" not in source
    assert "${ENTERPRISE_RAG_IMAGE:-enterprise-rag-engine:local}" in source


def test_copilot_image_contains_controlled_ap_policy_bundle_without_enabling_execution() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    manifest = (PROJECT_ROOT / "data/policies/accounts_payable/v1/corpus-manifest.json").read_text(
        encoding="utf-8"
    )

    assert "COPY data/policies ./data/policies" in dockerfile
    assert "AP_POLICY_BUNDLE_DIR=/app/data/policies/accounts_payable/v1" in dockerfile
    assert "POLICY_SNAPSHOT_DIR=/app/data/policy-snapshots" in dockerfile
    assert '"policy_profile": "accounts_payable_policy.v1"' in manifest
