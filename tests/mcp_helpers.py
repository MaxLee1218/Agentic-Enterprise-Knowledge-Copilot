"""Shared factories for hermetic Stage 18 MCP tests."""

from __future__ import annotations

import socket
import sys
import time
from datetime import timedelta
from pathlib import Path

from copilot.contracts import (
    JsonObject,
    MCPCapabilityType,
    MCPClientIdentity,
    MCPConnection,
    MCPInvocation,
    MCPInvocationContext,
    MCPServerIdentity,
    MCPStdioConfiguration,
    MCPToolCapability,
    MCPTransport,
)
from copilot.contracts.validators import utc_now

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_SERVER = PROJECT_ROOT / "tests" / "fixtures" / "mcp" / "real_test_server.py"


def identity(*, tenant_id: str = "tenant-alpha") -> MCPClientIdentity:
    return MCPClientIdentity(
        client_id="mcp-test-client",
        user_id="quality-analyst",
        tenant_id=tenant_id,
        roles=("quality_analyst",),
        scopes=(
            "mcp.tools.read",
            "mcp.tools.invoke",
            "mcp.resources.read",
            "mcp.prompts.read",
        ),
        data_scope=("supplier_quality",),
        purpose="supplier_quality_analysis.v1",
        authentication_source="hermetic_test",
    )


def stdio_connection(*, server_id: str = "stage18-real-test-server") -> MCPConnection:
    return MCPConnection(
        connection_id="stage18-stdio-test",
        server=MCPServerIdentity(server_id=server_id, display_name="Stage 18 test server"),
        namespace="stage18test",
        transport=MCPTransport.STDIO,
        stdio=MCPStdioConfiguration(
            executable=sys.executable,
            arguments=(str(REAL_SERVER), "--transport", "stdio"),
            working_directory=str(PROJECT_ROOT),
            environment=JsonObject({}),
        ),
        allowed_tenants=("tenant-alpha",),
    )


def http_connection(port: int, *, credential_reference: str | None = None) -> MCPConnection:
    return MCPConnection(
        connection_id="stage18-http-test",
        server=MCPServerIdentity(
            server_id="stage18-real-test-server", display_name="Stage 18 HTTP test server"
        ),
        namespace="stage18http",
        transport=MCPTransport.STREAMABLE_HTTP,
        endpoint=f"http://127.0.0.1:{port}/mcp",
        credential_reference=credential_reference,
        allowed_tenants=("tenant-alpha",),
    )


def invocation(
    capability: MCPToolCapability,
    *,
    session_id: str,
    text: str = "hello",
) -> MCPInvocation:
    principal = identity()
    return MCPInvocation(
        invocation_id="MCPINV-test-0001",
        capability=capability,
        arguments=JsonObject({"text": text}),
        context=MCPInvocationContext(
            connection_id=capability.origin.connection_id,
            session_id=session_id,
            server_id=capability.origin.server_id,
            namespace=capability.namespace,
            capability_name=capability.name,
            capability_type=MCPCapabilityType.TOOL,
            client_identity=principal,
            task_id="TASK-MCP-TEST",
            trace_id="TRACE-MCP-TEST",
            step_id="STEP-MCP-TEST",
            tool_call_id="CALL-MCP-TEST",
            deadline_at=utc_now() + timedelta(seconds=30),
        ),
    )


def unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_port(port: int, *, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"test server did not listen on port {port}")


def find_tool(capabilities: tuple[object, ...], name: str) -> MCPToolCapability:
    for capability in capabilities:
        if isinstance(capability, MCPToolCapability) and capability.name == name:
            return capability
    raise AssertionError(f"missing MCP test tool: {name}")


__all__ = [
    "PROJECT_ROOT",
    "REAL_SERVER",
    "find_tool",
    "http_connection",
    "identity",
    "invocation",
    "stdio_connection",
    "unused_tcp_port",
    "wait_for_port",
]
