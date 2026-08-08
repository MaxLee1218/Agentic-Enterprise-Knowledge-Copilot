"""Narrow pre-authorization and exact approval binding for the offline workflow."""

from collections.abc import Callable
from datetime import datetime

from copilot.contracts import ApprovalStatus, ToolCall, ToolDefinition
from copilot.contracts.validators import utc_now
from copilot.policies.approval import action_fingerprint, schema_fingerprint
from copilot.policies.data_access import (
    DataAccessPolicy,
    DataAccessRequest,
    access_profile_for_query_template,
)
from copilot.policies.permissions import AuthorizationRequest, Permission, PermissionMatrix
from copilot.services.approval_service import ApprovalRepositoryPort
from copilot.services.execution import ExecutionContext
from copilot.tools.exceptions import ToolAuthorizationError
from copilot.tools.schema import validate_payload


class OfflineSupplierQualityAuthorizer:
    """Authorize only trusted mock calls bound to their own tenant and frozen schemas."""

    def __init__(
        self,
        approval_repository: ApprovalRepositoryPort | None = None,
        *,
        clock: Callable[[], datetime] = utc_now,
        permission_matrix: PermissionMatrix | None = None,
        data_access_policy: DataAccessPolicy | None = None,
    ) -> None:
        self._approval_repository = approval_repository
        self._clock = clock
        self._permission_matrix = permission_matrix or PermissionMatrix()
        self._data_access_policy = data_access_policy or DataAccessPolicy()

    def authorize_with_context(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        execution_context: ExecutionContext,
    ) -> None:
        """Re-authorize every real attempt from the current trusted execution context."""
        if (
            not execution_context.authenticated
            or execution_context.task_id != call.task_id
            or execution_context.step_id != call.step_id
            or execution_context.user_id != call.user_id
            or execution_context.tenant_id != call.tenant_id
            or execution_context.deadline_at != call.deadline_at
            or execution_context.approval_id != call.approval_id
        ):
            raise ToolAuthorizationError(
                "Security context does not match the invocation",
                error_code="UNKNOWN_PRINCIPAL",
            )
        self._authorize(
            call,
            definition,
            roles=execution_context.roles,
            is_demo_identity=execution_context.is_demo_identity,
            purpose=execution_context.purpose,
        )
        if execution_context.approval_required and call.approval_id is None:
            raise ToolAuthorizationError(
                "A bound approval is required for this invocation",
                error_code="APPROVAL_REQUIRED",
            )

    def _authorize(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        *,
        roles: tuple[str, ...],
        is_demo_identity: bool,
        purpose: str,
    ) -> None:
        """Fail closed if identity, role, tenant, data scope, or approval does not match."""
        if not call.user_id or not call.tenant_id:
            raise ToolAuthorizationError(
                "Authenticated user and tenant are required", error_code="UNKNOWN_PRINCIPAL"
            )
        tool_decision = self._permission_matrix.evaluate(
            AuthorizationRequest(
                action=Permission.EXECUTE_TOOL,
                roles=roles,
                resource_type="tool",
                resource_name=call.tool_name,
                tool_name=call.tool_name,
                task_id=call.task_id,
                purpose=purpose,
                is_demo_identity=is_demo_identity,
            )
        )
        if not tool_decision.allowed:
            raise ToolAuthorizationError(
                tool_decision.reason,
                error_code=tool_decision.reason_code,
            )
        if call.tool_name != definition.tool_name:
            raise ToolAuthorizationError("Tool definition does not match the requested call")
        validate_payload(call.input, definition.input_schema.root, "authorized input")
        if call.tool_name == "database_query":
            parameters = call.input.root.get("parameters")
            if not isinstance(parameters, dict) or parameters.get("tenant_id") != call.tenant_id:
                raise ToolAuthorizationError("Database tenant scope does not match the call")
            template_id = call.input.root.get("query_template_id")
            if not isinstance(template_id, str):
                raise ToolAuthorizationError(
                    "Database template is not authorized", error_code="TABLE_NOT_ALLOWED"
                )
            table_names, field_names = access_profile_for_query_template(template_id)
            if not table_names or not field_names:
                raise ToolAuthorizationError(
                    "Database template is not authorized", error_code="TABLE_NOT_ALLOWED"
                )
            data_decision = self._data_access_policy.evaluate(
                DataAccessRequest(
                    roles=roles,
                    table_names=table_names,
                    field_names=field_names,
                    purpose=purpose,
                    is_demo_identity=is_demo_identity,
                )
            )
            if not data_decision.allowed:
                raise ToolAuthorizationError(
                    data_decision.reason,
                    error_code=data_decision.reason_code,
                )
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
                approval = self._approval_repository.get(
                    call.approval_id,
                    tenant_id=call.tenant_id,
                )
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
