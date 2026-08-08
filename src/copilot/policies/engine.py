"""Policy boundary implementations used by governed execution runtimes."""

from copilot.contracts import ToolCall, ToolDefinition
from copilot.services.execution import ExecutionContext
from copilot.tools.exceptions import ToolAuthorizationError


class DenyByDefaultToolAuthorizer:
    """Safe placeholder that prevents execution until a real policy decision is injected."""

    def authorize_with_context(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        execution_context: ExecutionContext,
    ) -> None:
        """Reject every call; production policy integration must replace this implementation."""
        del call, definition, execution_context
        raise ToolAuthorizationError()
