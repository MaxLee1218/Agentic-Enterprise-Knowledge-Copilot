"""Stage 17.1 identity-provider and transport-boundary security tests."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from copilot.api.app import create_app
from copilot.bootstrap.container import build_workflow_container
from copilot.config import Settings
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.persistence.identifiers import SequentialIdentifierFactory
from copilot.security.identity import DemoIdentityProvider, TrustedHeaderIdentityProvider
from copilot.services.identity import IdentityRequest, IdentityResolutionError
from tests.workflow_helpers import fixed_clock

SECRET = "stage17-identity-signing-secret-value"


def _headers(*, secret: str = SECRET, tenant_id: str = "TENANT-A") -> dict[str, str]:
    values = {
        "x-copilot-user-id": "U-AUTHENTICATED",
        "x-copilot-tenant-id": tenant_id,
        "x-copilot-roles": "quality_analyst",
        "x-copilot-scopes": "task:execute,data:quality.v1,evidence:read",
        "x-copilot-supplier-ids": "SUP-001,SUP-002",
        "x-copilot-purpose": "supplier_quality_analysis.v1",
        "x-copilot-identity-timestamp": "1000",
    }
    canonical = TrustedHeaderIdentityProvider.canonical_assertion(values)
    values["x-copilot-identity-signature"] = hmac.new(
        secret.encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()
    return values


def _ap_headers(*, secret: str = SECRET) -> dict[str, str]:
    values = {
        "x-copilot-user-id": "U-FINANCE",
        "x-copilot-tenant-id": "TENANT-A",
        "x-copilot-roles": "finance_analyst,finance_auditor",
        "x-copilot-scopes": (
            "task:execute,data:accounts_payable.v1,data:accounts-payable-policy-v1,"
            "finance:ap.detail,finance:ap.artifact:download"
        ),
        "x-copilot-supplier-ids": "SUP-001",
        "x-copilot-legal-entity-ids": "LE-001",
        "x-copilot-business-unit-ids": "BU-001,BU-002",
        "x-copilot-currency-scope": "USD,CNY",
        "x-copilot-assigned-task-ids": "T-ASSIGNED-001",
        "x-copilot-allowed-task-types": "accounts_payable_analysis.v1",
        "x-copilot-purpose": "accounts_payable_analysis.v1",
        "x-copilot-policy-rule-set-id": "accounts-payable-v1",
        "x-copilot-policy-rule-set-version": "ap_rules.2026.1",
        "x-copilot-policy-manifest-checksum": "sha256:manifest",
        "x-copilot-policy-materiality": "USD=1000,CNY=5000",
        "x-copilot-policy-snapshot-at": "2026-07-01T00:00:00Z",
        "x-copilot-policy-requires-approval": "false",
        "x-copilot-identity-timestamp": "1000",
    }
    canonical = TrustedHeaderIdentityProvider.canonical_assertion(values)
    values["x-copilot-identity-signature"] = hmac.new(
        secret.encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()
    return values


def test_development_demo_is_explicit_and_production_demo_is_rejected() -> None:
    provider = DemoIdentityProvider(Settings(app_env="development", database_url="sqlite:///x"))
    identity = provider.resolve(IdentityRequest(headers={}, source="test"))
    assert identity.is_demo_identity is True
    assert identity.roles == ("quality_analyst",)

    production = Settings.model_construct(app_env="production")
    with pytest.raises(IdentityResolutionError, match="forbidden"):
        DemoIdentityProvider(production)


def test_local_enterprise_demo_profile_has_server_owned_multi_domain_authority() -> None:
    provider = DemoIdentityProvider(
        Settings(
            app_env="development",
            database_url="sqlite:///x",
            demo_identity_profile="local_enterprise",
        )
    )

    identity = provider.resolve(IdentityRequest(headers={}, source="test"))

    assert tuple(item.value for item in identity.allowed_task_types) == (
        "supplier_quality_analysis.v1",
        "accounts_payable_analysis.v1",
    )
    assert identity.data_scope == (
        "quality.v1",
        "supplier-quality-policy-v1",
        "accounts_payable.v1",
        "accounts-payable-policy-v1",
    )
    assert identity.legal_entity_ids == ("LE-CN-01", "LE-US-01")
    assert identity.currency_scope == ("CNY", "USD")
    assert {"finance_analyst", "finance_approver"}.issubset(identity.roles)
    assert {
        "finance:ap.detail",
        "finance:ap.artifact:download",
        "approvals:resolve",
    }.issubset(identity.scopes)
    assert identity.policy_rule_set_version == "ap_rules.2026.1"
    assert identity.policy_snapshot_at is not None


def test_signed_identity_preserves_authority_and_rejects_missing_tampered_or_stale_input() -> None:
    provider = TrustedHeaderIdentityProvider(SECRET, clock=lambda: 1000)
    identity = provider.resolve(IdentityRequest(headers=_headers(), source="api"))

    assert identity.user_id == "U-AUTHENTICATED"
    assert identity.tenant_id == "TENANT-A"
    assert identity.roles == ("quality_analyst",)
    assert identity.scopes == ("task:execute", "data:quality.v1", "evidence:read")
    assert identity.data_scope == ("quality.v1",)
    assert identity.supplier_ids == ("SUP-001", "SUP-002")
    assert identity.authenticated is True
    assert identity.is_demo_identity is False

    with pytest.raises(IdentityResolutionError):
        provider.resolve(IdentityRequest(headers={}, source="api"))
    tampered = _headers()
    tampered["x-copilot-roles"] = "quality_data_approver"
    with pytest.raises(IdentityResolutionError, match="signature"):
        provider.resolve(IdentityRequest(headers=tampered, source="api"))
    stale_provider = TrustedHeaderIdentityProvider(SECRET, max_age_seconds=30, clock=lambda: 2000)
    with pytest.raises(IdentityResolutionError, match="validity"):
        stale_provider.resolve(IdentityRequest(headers=_headers(), source="api"))


def test_signed_finance_identity_carries_all_server_owned_ap_dimensions() -> None:
    provider = TrustedHeaderIdentityProvider(SECRET, clock=lambda: 1000)
    identity = provider.resolve(IdentityRequest(headers=_ap_headers(), source="api"))

    assert identity.purpose == "accounts_payable_analysis.v1"
    assert tuple(item.value for item in identity.allowed_task_types) == (
        "accounts_payable_analysis.v1",
    )
    assert identity.legal_entity_ids == ("LE-001",)
    assert identity.business_unit_ids == ("BU-001", "BU-002")
    assert identity.currency_scope == ("USD", "CNY")
    assert identity.assigned_task_ids == ("T-ASSIGNED-001",)
    assert identity.policy_rule_set_version == "ap_rules.2026.1"
    assert tuple(item.currency for item in identity.policy_materiality) == ("USD", "CNY")
    assert identity.policy_snapshot_at is not None

    tampered = _ap_headers()
    tampered["x-copilot-legal-entity-ids"] = "LE-OTHER"
    with pytest.raises(IdentityResolutionError, match="signature"):
        provider.resolve(IdentityRequest(headers=tampered, source="api"))


def test_api_requires_signed_identity_and_propagates_it_to_the_task_context(
    tmp_path: Path,
) -> None:
    root = tmp_path
    settings = Settings(
        app_env="test",
        database_url="sqlite:///unused-identity-api.db",
        artifact_dir=root / "artifacts",
        checkpoint_database_path=root / "checkpoints.db",
        checkpoint_enabled=False,
        identity_provider="trusted_headers",
        identity_signing_secret=SecretStr(SECRET),
    )
    container = build_workflow_container(
        settings,
        ids=SequentialIdentifierFactory(),
        clock=fixed_clock,
        sleeper=lambda _seconds: None,
        llm_provider=OfflineMockLLM(),
    )
    provider = TrustedHeaderIdentityProvider(SECRET, clock=lambda: 1000)
    client = TestClient(
        create_app(
            task_service=container.task_service,
            settings=settings,
            observability=container.observability,
            identity_provider=provider,
        )
    )
    try:
        with client:
            missing = client.post(
                "/v1/tasks",
                json={"task": "Analyze Q2 2026 supplier quality and generate a JSON report."},
            )
            accepted = client.post(
                "/v1/tasks",
                headers=_headers(),
                json={"task": "Analyze Q2 2026 supplier quality and generate a JSON report."},
            )
        assert missing.status_code == 401
        assert accepted.status_code == 201
        task_id = accepted.json()["task_id"]
        state = container.engine.get_state(task_id, "TENANT-A")
        context = state["intake_context"]
        assert context.user_id == "U-AUTHENTICATED"
        assert context.tenant_id == "TENANT-A"
        assert context.roles == ("quality_analyst",)
        assert context.scopes == ("task:execute", "data:quality.v1", "evidence:read")
    finally:
        container.close()
