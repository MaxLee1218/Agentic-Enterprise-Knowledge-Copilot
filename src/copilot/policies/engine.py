"""Policy boundary implementations used by governed execution runtimes."""

from copilot.contracts import ToolCall, ToolDefinition
from copilot.tools.exceptions import ToolAuthorizationError


class DenyByDefaultToolAuthorizer:
    """Safe placeholder that prevents execution until a real policy decision is injected."""

    def authorize(self, call: ToolCall, definition: ToolDefinition) -> None:
        """Reject every call; production policy integration must replace this implementation."""
        del call, definition
        raise ToolAuthorizationError()
