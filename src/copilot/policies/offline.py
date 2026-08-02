"""Narrow pre-authorization and exact approval binding for the offline workflow."""

from collections.abc import Callable
from datetime import datetime

from copilot.contracts import ApprovalStatus, ToolCall, ToolDefinition
from copilot.contracts.validators import utc_now
from copilot.policies.approval import action_fingerprint, schema_fingerprint
from copilot.services.approval_service import ApprovalRepositoryPort
from copilot.tools.exceptions import ToolAuthorizationError
from copilot.tools.schema import validate_payload


class OfflineSupplierQualityAuthorizer:
    """Authorize only trusted mock calls bound to their own tenant and frozen schemas."""

    def __init__(
        self,
        approval_repository: ApprovalRepositoryPort | None = None,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._approval_repository = approval_repository
        self._clock = clock

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
        if call.approval_id is not None:
            if self._approval_repository is None:
                raise ToolAuthorizationError("Approval validation is unavailable")
            try:
                approval = self._approval_repository.get(call.approval_id)
            except KeyError as exc:
                raise ToolAuthorizationError("Approval record was not found") from exc
            schema_digest = schema_fingerprint(definition)
            expected_fingerprint = action_fingerprint(
                task_id=approval.task_id,
                planning_version=approval.planning_version,
                step_id=approval.step_id,
                tool_name=approval.tool_name,
                tool_version=approval.tool_version,
                input_schema_fingerprint=approval.input_schema_fingerprint,
                controlled_scope=approval.controlled_scope,
                arguments=call.input,
            )
            if (
                approval.status is not ApprovalStatus.APPROVED
                or approval.expires_at <= self._clock()
                or approval.task_id != call.task_id
                or approval.tenant_id != call.tenant_id
                or approval.step_id != call.step_id
                or approval.tool_name != call.tool_name
                or approval.tool_version != call.tool_version
                or approval.input_schema_fingerprint != schema_digest
                or approval.resolved_arguments != call.input
                or approval.resolved_action_fingerprint != expected_fingerprint
            ):
                raise ToolAuthorizationError("Approval does not cover this exact invocation")
