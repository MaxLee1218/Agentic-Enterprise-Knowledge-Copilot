"""SDK-independent MCP server dispatch composed from governed providers."""

from __future__ import annotations

from copilot.contracts import (
    JsonObject,
    MCPClientIdentity,
    MCPInvocationResult,
    MCPPromptCapability,
    MCPResourceCapability,
    MCPToolCapability,
)
from copilot.mcp.protocol import MCPPromptResult, MCPResourceContent
from copilot.mcp.server.prompt_provider import MCPPromptProvider
from copilot.mcp.server.resource_provider import MCPResourceProvider
from copilot.mcp.server.tool_provider import MCPToolProvider


class MCPServerApplication:
    """Thin provider dispatch; dependencies are injected by the composition root."""

    def __init__(
        self,
        *,
        tools: MCPToolProvider,
        resources: MCPResourceProvider,
        prompts: MCPPromptProvider,
    ) -> None:
        self._tools = tools
        self._resources = resources
        self._prompts = prompts

    def list_tools(self, identity: MCPClientIdentity) -> tuple[MCPToolCapability, ...]:
        return self._tools.list_tools(identity)

    def invoke_tool(
        self,
        name: str,
        arguments: JsonObject,
        identity: MCPClientIdentity,
        metadata: JsonObject,
    ) -> MCPInvocationResult:
        return self._tools.invoke(name, arguments, identity, metadata)

    def list_resources(self, identity: MCPClientIdentity) -> tuple[MCPResourceCapability, ...]:
        return self._resources.list(identity)

    def read_resource(self, uri: str, identity: MCPClientIdentity) -> MCPResourceContent:
        return self._resources.read(uri, identity)

    def list_prompts(self, identity: MCPClientIdentity) -> tuple[MCPPromptCapability, ...]:
        return self._prompts.list(identity)

    def get_prompt(
        self, name: str, arguments: JsonObject, identity: MCPClientIdentity
    ) -> MCPPromptResult:
        return self._prompts.get(name, arguments, identity)


__all__ = ["MCPServerApplication"]
