from __future__ import annotations

import subprocess
import sys

import pytest

from copilot.contracts import JsonObject, MCPPromptCapability, MCPResourceCapability
from copilot.mcp.errors import MCPNegotiationError
from copilot.mcp.protocol import PINNED_PROTOCOL_REVISION, MCPProtocolClient
from tests.mcp_helpers import (
    PROJECT_ROOT,
    REAL_SERVER,
    find_tool,
    http_connection,
    invocation,
    stdio_connection,
    unused_tcp_port,
    wait_for_port,
)


@pytest.mark.integration
def test_real_stdio_initialization_discovery_invoke_resource_prompt_and_isolation() -> None:
    connection = stdio_connection()
    client = MCPProtocolClient(connection)
    try:
        discovered = client.connect()
        assert discovered.negotiated.protocol_revision is PINNED_PROTOCOL_REVISION
        assert {"tools", "resources", "prompts"}.issubset(discovered.negotiated.server_capabilities)
        echo = find_tool(discovered.negotiated.capabilities, "echo")
        malicious = find_tool(discovered.negotiated.capabilities, "malicious_metadata_254f917d")
        assert malicious.description == "[QUARANTINED UNTRUSTED CONTENT]"
        result = client.invoke(
            invocation(echo, session_id=discovered.session_id, text="real-stdio-round-trip")
        )
        assert result.output == JsonObject({"echoed": "real-stdio-round-trip"})

        resource = next(
            item
            for item in discovered.negotiated.capabilities
            if isinstance(item, MCPResourceCapability)
        )
        assert client.read_resource(resource)[0].text == "approved test policy"
        prompt = next(
            item
            for item in discovered.negotiated.capabilities
            if isinstance(item, MCPPromptCapability)
        )
        prompt_result = client.get_prompt(prompt, JsonObject({"topic": "supplier quality"}))
        assert "supplier quality" in str(prompt_result.messages[0].root)
    finally:
        client.close()


@pytest.mark.integration
def test_real_streamable_http_round_trip_and_session_id() -> None:
    port = unused_tcp_port()
    process = subprocess.Popen(
        [sys.executable, str(REAL_SERVER), "--transport", "http", "--port", str(port)],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    client: MCPProtocolClient | None = None
    try:
        wait_for_port(port)
        client = MCPProtocolClient(http_connection(port))
        discovered = client.connect()
        assert len(discovered.session_id) == 32
        assert discovered.session_id.isalnum()
        echo = find_tool(discovered.negotiated.capabilities, "echo")
        result = client.invoke(
            invocation(echo, session_id=discovered.session_id, text="real-http-round-trip")
        )
        assert result.output == JsonObject({"echoed": "real-http-round-trip"})
    finally:
        if client is not None:
            client.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    assert process.returncode in {0, -15}


@pytest.mark.integration
def test_real_server_identity_mismatch_fails_negotiation() -> None:
    client = MCPProtocolClient(stdio_connection(server_id="unexpected-server"))
    try:
        with pytest.raises(MCPNegotiationError):
            client.connect()
    finally:
        client.close()
