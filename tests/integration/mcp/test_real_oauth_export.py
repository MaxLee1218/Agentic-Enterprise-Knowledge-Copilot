from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from copilot.contracts import JsonObject, MCPConnection, MCPServerIdentity, MCPTransport
from copilot.mcp.errors import MCPConnectionError
from copilot.mcp.protocol import MCPProtocolClient
from tests.mcp_helpers import PROJECT_ROOT, find_tool, invocation, unused_tcp_port, wait_for_port

OAUTH_SERVER = PROJECT_ROOT / "tests" / "fixtures" / "mcp" / "governed_oauth_server.py"
SIGNING_KEY = "stage18-hermetic-signing-key-32-bytes-minimum"


def _token(*, tenant_id: str = "tenant-alpha", audience: str = "copilot-mcp-test") -> str:
    now = datetime.now(UTC)
    return str(
        jwt.encode(
            {
                "iss": "https://issuer.example.test",
                "aud": audience,
                "sub": "quality-analyst",
                "client_id": "stage18-test-client",
                "user_id": "quality-analyst",
                "tenant_id": tenant_id,
                "scope": "mcp.tools.read mcp.tools.invoke",
                "roles": ["quality_analyst"],
                "data_scope": ["quality.v1"],
                "purpose": "supplier_quality_analysis.v1",
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=5)).timestamp()),
            },
            SIGNING_KEY,
            algorithm="HS256",
        )
    )


def _connection(port: int) -> MCPConnection:
    return MCPConnection(
        connection_id="copilot-oauth-test",
        server=MCPServerIdentity(
            server_id="agentic-enterprise-knowledge-copilot",
            display_name="Governed Copilot MCP server",
        ),
        namespace="copilotremote",
        transport=MCPTransport.STREAMABLE_HTTP,
        endpoint=f"http://127.0.0.1:{port}/mcp",
        credential_reference="env:MCP_TEST_BEARER_TOKEN",
        allowed_tenants=("tenant-alpha",),
    )


def _knowledge_arguments() -> JsonObject:
    return JsonObject(
        {
            "query": "supplier defect policy",
            "tenant_id": "tenant-alpha",
            "collection_ids": ["quality.v1"],
            "supplier_ids": ["SUP-001"],
            "date_range": {"start": "2026-04-01", "end": "2026-06-30"},
            "top_k": 5,
            "index_snapshot_id": "quality-policy-v1",
        }
    )


@pytest.mark.integration
def test_real_oauth_export_invokes_existing_governed_tool_and_denies_bad_identity() -> None:
    port = unused_tcp_port()
    environment = dict(os.environ)
    environment["MCP_TEST_SIGNING_KEY"] = SIGNING_KEY
    process = subprocess.Popen(
        [sys.executable, str(OAUTH_SERVER), "--port", str(port)],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    clients: list[MCPProtocolClient] = []
    try:
        wait_for_port(port)
        client = MCPProtocolClient(_connection(port))
        clients.append(client)
        discovered = client.connect(credential=_token())
        tool = find_tool(discovered.negotiated.capabilities, "knowledge_search")
        request = invocation(tool, session_id=discovered.session_id)
        result = client.invoke(request.model_copy(update={"arguments": _knowledge_arguments()}))
        assert result.output is not None
        assert result.output.root["match_count"] == 2

        wrong_tenant = MCPProtocolClient(_connection(port))
        clients.append(wrong_tenant)
        wrong_tenant_discovery = wrong_tenant.connect(credential=_token(tenant_id="tenant-beta"))
        assert not any(
            getattr(item, "name", None) == "knowledge_search"
            for item in wrong_tenant_discovery.negotiated.capabilities
        )

        invalid = MCPProtocolClient(_connection(port))
        clients.append(invalid)
        with pytest.raises(MCPConnectionError):
            invalid.connect(credential=_token(audience="wrong-audience"))
    finally:
        for client in clients:
            client.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    assert process.returncode in {0, -15}
