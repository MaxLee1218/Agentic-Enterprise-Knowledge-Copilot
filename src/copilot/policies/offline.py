"""Narrow pre-authorization policy for the offline deterministic mock workflow."""

from copilot.contracts import ToolCall, ToolDefinition
from copilot.tools.exceptions import ToolAuthorizationError
from copilot.tools.schema import validate_payload


class OfflineSupplierQualityAuthorizer:
    """Authorize only trusted mock calls bound to their own tenant and frozen schemas."""

    def authorize(self, call: ToolCall, definition: ToolDefinition) -> None:
        """Fail closed if identity, tenant scope, or registered input does not match."""
        if not call.user_id or not call.tenant_id:
            raise ToolAuthorizationError("Authenticated user and tenant are required")
        if call.tool_name != definition.tool_name:
            raise ToolAuthorizationError("Tool definition does not match the requested call")
        validate_payload(call.input, definition.input_schema.root, "authorized input")
        if call.tool_name == "database_query":
            parameters = call.input.root.get("parameters")
            if not isinstance(parameters, dict) or parameters.get("tenant_id") != call.tenant_id:
                raise ToolAuthorizationError("Database tenant scope does not match the call")
        if call.tool_name == "knowledge_search":
            tenant = call.input.root.get("tenant_id")
            if tenant != call.tenant_id:
                raise ToolAuthorizationError("Knowledge tenant scope does not match the call")
        if call.tool_name == "report_generator":
            task_id = call.input.root.get("task_id")
            if task_id != call.task_id:
                raise ToolAuthorizationError("Report task scope does not match the call")
