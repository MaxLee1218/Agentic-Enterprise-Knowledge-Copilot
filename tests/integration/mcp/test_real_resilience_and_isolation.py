from __future__ import annotations

import subprocess
import sys
from time import monotonic

import httpx
import pytest

from copilot.mcp.errors import MCPCancelledError, MCPTimeoutError
from copilot.mcp.protocol import MCPProtocolClient
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

pytestmark = pytest.mark.integration


def test_per_server_sessions_are_isolated_and_survive_peer_disconnect() -> None:
    first_connection = stdio_connection().model_copy(
        update={"connection_id": "stage18-isolated-one", "namespace": "isolatedone"}
    )
    second_connection = stdio_connection().model_copy(
        update={"connection_id": "stage18-isolated-two", "namespace": "isolatedtwo"}
    )
    first = MCPProtocolClient(first_connection)
    second = MCPProtocolClient(second_connection)
    try:
        first_discovery = first.connect()
        second_discovery = second.connect()
        assert first_discovery.session_id != second_discovery.session_id
        first_echo = find_tool(first_discovery.negotiated.capabilities, "echo")
        second_echo = find_tool(second_discovery.negotiated.capabilities, "echo")
        assert first_echo.origin.connection_id == "stage18-isolated-one"
        assert second_echo.origin.connection_id == "stage18-isolated-two"
        first.close()
        result = second.invoke(
            invocation(second_echo, session_id=second_discovery.session_id, text="still-isolated")
        )
        assert result.output is not None and result.output.root["echoed"] == "still-isolated"
    finally:
        first.close()
        second.close()


def test_real_invocation_timeout_and_cancellation_are_bounded() -> None:
    timeout_client = MCPProtocolClient(stdio_connection(), invocation_timeout_seconds=0.05)
    try:
        discovered = timeout_client.connect()
        slow = find_tool(discovered.negotiated.capabilities, "slow")
        with pytest.raises(MCPTimeoutError):
            timeout_client.invoke(invocation(slow, session_id=discovered.session_id))
    finally:
        timeout_client.close()

    cancellation_client = MCPProtocolClient(stdio_connection())
    try:
        discovered = cancellation_client.connect()
        slow = find_tool(discovered.negotiated.capabilities, "slow")
        started = monotonic()
        with pytest.raises(MCPCancelledError):
            cancellation_client.invoke(
                invocation(slow, session_id=discovered.session_id),
                cancellation_requested=lambda: monotonic() - started > 0.05,
            )
    finally:
        cancellation_client.close()


def test_streamable_http_rejects_invalid_json_rpc_and_unapproved_origin() -> None:
    port = unused_tcp_port()
    process = subprocess.Popen(
        [sys.executable, str(REAL_SERVER), "--transport", "http", "--port", str(port)],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_for_port(port)
        endpoint = http_connection(port).endpoint
        assert endpoint is not None
        with httpx.Client(trust_env=False) as client:
            invalid = client.post(
                endpoint,
                content=b'{"jsonrpc":"2.0","id":1,"method":',
                headers={
                    "accept": "application/json, text/event-stream",
                    "content-type": "application/json",
                },
            )
            assert invalid.status_code in {400, 422}
            hostile_origin = client.post(
                endpoint,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "hostile", "version": "1"},
                    },
                },
                headers={
                    "accept": "application/json, text/event-stream",
                    "origin": "https://attacker.invalid",
                },
            )
            assert hostile_origin.status_code in {403, 421}
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    assert process.returncode in {0, -15}
