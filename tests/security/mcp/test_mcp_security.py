from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr

from copilot.mcp.errors import MCPAuthenticationError, MCPCapabilityNotFoundError
from copilot.mcp.security.credential_provider import EnvCredentialProvider
from copilot.mcp.server.authorization import JWTAuthorizationVerifier, MCPServerAuthorization
from copilot.mcp.server.capability_exporter import MCPCapabilityExporter
from copilot.tools.registry import ToolRegistry
from tests.mcp_helpers import identity

KEY = "stage18-hermetic-signing-key-32-bytes-minimum"


def _jwt() -> str:
    now = datetime.now(UTC)
    return str(
        jwt.encode(
            {
                "iss": "https://issuer.example.test",
                "aud": "copilot-mcp-test",
                "sub": "quality-analyst",
                "client_id": "stage18-test-client",
                "user_id": "quality-analyst",
                "tenant_id": "tenant-alpha",
                "scope": "mcp.tools.read mcp.tools.invoke",
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=5)).timestamp()),
            },
            KEY,
            algorithm="HS256",
        )
    )


def test_verified_token_is_reduced_to_claims_and_fingerprint_without_leakage() -> None:
    raw_token = _jwt()
    verifier = JWTAuthorizationVerifier(
        signing_key=SecretStr(KEY),
        issuer="https://issuer.example.test",
        audience="copilot-mcp-test",
    )
    verified = verifier.verify(raw_token)
    assert verified is not None
    serialized = verified.model_dump_json()
    assert raw_token not in serialized
    assert KEY not in serialized
    assert len(verified.token_fingerprint) == 64
    assert verifier.verify(raw_token + "tampered") is None


def test_credential_provider_requires_explicit_reference_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_APPROVED_TOKEN", "runtime-only-secret")
    provider = EnvCredentialProvider(allowed_names=("MCP_APPROVED_TOKEN",))
    secret = provider.resolve("env:MCP_APPROVED_TOKEN")
    assert secret is not None
    assert "runtime-only-secret" not in repr(secret)
    with pytest.raises(MCPAuthenticationError):
        provider.resolve("env:MCP_UNAPPROVED_TOKEN")


def test_empty_export_allowlist_blocks_discovery_and_privilege_escalation() -> None:
    exporter = MCPCapabilityExporter(
        registry=ToolRegistry(),
        rules=(),
        authorization=MCPServerAuthorization(),
        server_id="copilot-mcp-server",
        namespace="copilot",
    )
    assert exporter.list(identity()) == ()
    with pytest.raises(MCPCapabilityNotFoundError):
        exporter.require_invocation("database_query", identity())
