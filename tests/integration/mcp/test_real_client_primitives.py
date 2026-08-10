from __future__ import annotations

from pathlib import Path

import pytest

from copilot.contracts import JsonObject, MCPCapabilityType
from copilot.mcp.client.elicitation_handler import MCPElicitationHandler
from copilot.mcp.client.roots_provider import MCPRootsProvider
from copilot.mcp.client.sampling_handler import MCPSamplingHandler
from copilot.mcp.errors import MCPAuthorizationError, MCPInvalidResponseError
from copilot.mcp.protocol import MCPProtocolClient
from copilot.policies.mcp_access import MCPAccessPolicy, MCPAccessRule
from tests.mcp_helpers import find_tool, identity, invocation, stdio_connection

pytestmark = pytest.mark.integration


def _primitive_policy(*, allowed: bool) -> MCPAccessPolicy:
    connection = stdio_connection()
    return MCPAccessPolicy(
        (
            MCPAccessRule(
                connection_id=connection.connection_id,
                server_id=connection.server.server_id,
                namespace=connection.namespace,
                tenants=frozenset({"tenant-alpha"}),
                capability_names=frozenset({"sampling", "elicitation"}) if allowed else frozenset(),
                capability_types=frozenset(
                    {MCPCapabilityType.SAMPLING, MCPCapabilityType.ELICITATION}
                ),
                allow_sampling=allowed,
                allow_elicitation=allowed,
            ),
        )
    )


def test_sampling_elicitation_and_roots_are_policy_gated_by_default(tmp_path: Path) -> None:
    connection = stdio_connection()
    sampler = MCPSamplingHandler(
        policy=_primitive_policy(allowed=False),
        connection_id=connection.connection_id,
        server_id=connection.server.server_id,
        namespace=connection.namespace,
        identity=identity(),
        sampler=lambda _request: JsonObject({}),
    )
    with pytest.raises(MCPAuthorizationError):
        sampler(JsonObject({"messages": []}))

    elicitor = MCPElicitationHandler(
        policy=_primitive_policy(allowed=True),
        connection_id=connection.connection_id,
        server_id=connection.server.server_id,
        namespace=connection.namespace,
        identity=identity(),
        responder=lambda _request: JsonObject({"action": "decline"}),
    )
    with pytest.raises(MCPInvalidResponseError):
        elicitor(JsonObject({"message": "provide API_TOKEN"}))

    approved_root = tmp_path / "tenant-alpha-documents"
    approved_root.mkdir()
    roots = MCPRootsProvider(
        tenant_id="tenant-alpha", approved_roots=(("tenant-alpha", approved_root),)
    )()
    assert roots == ((approved_root.as_uri(), approved_root.name),)
    with pytest.raises(Exception, match="Broad host roots"):
        MCPRootsProvider(tenant_id="tenant-alpha", approved_roots=(("tenant-alpha", Path("/")),))


def test_real_sampling_elicitation_roots_and_progress_callbacks(tmp_path: Path) -> None:
    connection = stdio_connection()
    policy = _primitive_policy(allowed=True)
    sampling_requests: list[JsonObject] = []
    elicitation_requests: list[JsonObject] = []
    progress: list[tuple[float, float | None, str | None]] = []
    notifications: list[str] = []

    def record_notification(event: str) -> None:
        notifications.append(event)

    def sample(request: JsonObject) -> JsonObject:
        sampling_requests.append(request)
        return JsonObject(
            {
                "role": "assistant",
                "content": {"type": "text", "text": "bounded sample"},
                "model": "hermetic-test-model",
                "stopReason": "endTurn",
            }
        )

    def elicit(request: JsonObject) -> JsonObject:
        elicitation_requests.append(request)
        return JsonObject({"action": "accept", "content": {"label": "approved"}})

    root = tmp_path / "approved-root"
    root.mkdir()
    client = MCPProtocolClient(
        connection,
        sampling_callback=MCPSamplingHandler(
            policy=policy,
            connection_id=connection.connection_id,
            server_id=connection.server.server_id,
            namespace=connection.namespace,
            identity=identity(),
            sampler=sample,
        ),
        elicitation_callback=MCPElicitationHandler(
            policy=policy,
            connection_id=connection.connection_id,
            server_id=connection.server.server_id,
            namespace=connection.namespace,
            identity=identity(),
            responder=elicit,
        ),
        roots_callback=MCPRootsProvider(
            tenant_id="tenant-alpha", approved_roots=(("tenant-alpha", root),)
        ),
        notification_callback=record_notification,
    )
    try:
        discovered = client.connect()
        for name in ("sample", "elicit", "roots"):
            tool = find_tool(discovered.negotiated.capabilities, name)
            result = client.invoke(invocation(tool, session_id=discovered.session_id))
            assert result.output is not None
        progress_tool = find_tool(discovered.negotiated.capabilities, "progress")
        result = client.invoke(
            invocation(progress_tool, session_id=discovered.session_id),
            progress_callback=lambda current, total, message: progress.append(
                (current, total, message)
            ),
        )
        assert result.output == JsonObject({"progressed": True})
        assert progress == [(1.0, 2.0, "halfway"), (2.0, 2.0, "complete")]
        notify_tool = find_tool(discovered.negotiated.capabilities, "notify")
        notify_result = client.invoke(invocation(notify_tool, session_id=discovered.session_id))
        assert notify_result.output == JsonObject({"notified": True})
        assert set(notifications) == {
            "tools/list_changed",
            "resources/list_changed",
            "prompts/list_changed",
        }
        assert len(sampling_requests) == 1
        assert len(elicitation_requests) == 1
    finally:
        client.close()
