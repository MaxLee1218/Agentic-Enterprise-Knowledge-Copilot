"""Deny-by-default MCP access policy layered onto the existing tool authorizer."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, replace
from threading import RLock
from typing import Protocol

from copilot.contracts import (
    ApprovalStatus,
    MCPCapabilityType,
    MCPClientIdentity,
    ToolCall,
    ToolDefinition,
)
from copilot.contracts.validators import utc_now
from copilot.policies.approval import action_fingerprint, schema_fingerprint
from copilot.services.approval_service import ApprovalRepositoryPort
from copilot.services.execution import ExecutionContext
from copilot.tools.base import ToolAuthorizer
from copilot.tools.exceptions import ToolAuthorizationError


@dataclass(frozen=True, slots=True)
class MCPAccessRule:
    """One explicit connection/tenant/capability grant; empty sets grant nothing."""

    connection_id: str
    server_id: str
    namespace: str
    tenants: frozenset[str]
    capability_names: frozenset[str]
    capability_types: frozenset[MCPCapabilityType] = frozenset({MCPCapabilityType.TOOL})
    required_scopes: frozenset[str] = frozenset({"mcp.tools.invoke"})
    require_approval: bool = False
    allow_idempotent_retry: bool = False
    allow_sampling: bool = False
    allow_elicitation: bool = False


@dataclass(frozen=True, slots=True)
class MCPPolicyDecision:
    allowed: bool
    reason_code: str
    reason: str
    requires_approval: bool = False


class MCPAccessPolicy:
    """Thread-safe policy registry shared by lifecycle and tool authorization paths."""

    def __init__(self, rules: Collection[MCPAccessRule] = ()) -> None:
        self._rules = {rule.connection_id: rule for rule in rules}
        self._lock = RLock()

    def register(self, rule: MCPAccessRule) -> None:
        with self._lock:
            if rule.connection_id in self._rules:
                raise ValueError("MCP access rule already exists")
            self._rules[rule.connection_id] = rule

    def replace_capabilities(self, connection_id: str, capability_names: Collection[str]) -> None:
        """Narrow an existing configured allowlist to currently discovered capabilities."""
        with self._lock:
            rule = self._rules[connection_id]
            discovered = frozenset(capability_names)
            self._rules[connection_id] = replace(
                rule,
                capability_names=rule.capability_names.intersection(discovered),
            )

    def rule(self, connection_id: str) -> MCPAccessRule | None:
        with self._lock:
            return self._rules.get(connection_id)

    def rules(self) -> tuple[MCPAccessRule, ...]:
        """Return an immutable deterministic policy snapshot for authorization lookup."""
        with self._lock:
            return tuple(self._rules[key] for key in sorted(self._rules))

    def evaluate_connection(
        self,
        *,
        connection_id: str,
        server_id: str,
        namespace: str,
        identity: MCPClientIdentity,
    ) -> MCPPolicyDecision:
        rule = self.rule(connection_id)
        if rule is None:
            return _deny("MCP_CONNECTION_DENIED", "Connection has no access rule")
        if rule.server_id != server_id or rule.namespace != namespace:
            return _deny("MCP_ORIGIN_MISMATCH", "Connection origin does not match its rule")
        if identity.tenant_id not in rule.tenants:
            return _deny("MCP_TENANT_DENIED", "Tenant is not approved for the connection")
        if not rule.required_scopes.issubset(identity.scopes):
            return _deny("MCP_SCOPE_DENIED", "Required MCP scope is missing")
        return MCPPolicyDecision(True, "MCP_ALLOWED", "Connection is explicitly allowed")

    def evaluate_capability(
        self,
        *,
        connection_id: str,
        server_id: str,
        namespace: str,
        capability_name: str,
        capability_type: MCPCapabilityType,
        identity: MCPClientIdentity,
    ) -> MCPPolicyDecision:
        base = self.evaluate_connection(
            connection_id=connection_id,
            server_id=server_id,
            namespace=namespace,
            identity=identity,
        )
        if not base.allowed:
            return base
        rule = self.rule(connection_id)
        if rule is None:  # pragma: no cover
            return _deny("MCP_CONNECTION_DENIED", "Connection has no access rule")
        if capability_name not in rule.capability_names:
            return _deny("MCP_CAPABILITY_DENIED", "Capability is not explicitly allowlisted")
        if capability_type not in rule.capability_types:
            return _deny("MCP_CAPABILITY_TYPE_DENIED", "Capability type is not allowed")
        if capability_type is MCPCapabilityType.SAMPLING and not rule.allow_sampling:
            return _deny("MCP_SAMPLING_DISABLED", "Sampling is disabled")
        if capability_type is MCPCapabilityType.ELICITATION and not rule.allow_elicitation:
            return _deny("MCP_ELICITATION_DISABLED", "Elicitation is disabled")
        return MCPPolicyDecision(
            True,
            "MCP_ALLOWED",
            "Capability is explicitly allowed",
            requires_approval=rule.require_approval,
        )

    def allows_retry(
        self,
        *,
        connection_id: str,
        idempotent: bool,
        read_only: bool,
        destructive: bool,
    ) -> bool:
        """Require a local rule in addition to untrusted remote retry annotations."""
        rule = self.rule(connection_id)
        return bool(
            rule is not None
            and rule.allow_idempotent_retry
            and idempotent
            and read_only
            and not destructive
        )


class _RegistrationLookup(Protocol):
    def registration(self, name: str) -> object: ...


class MCPAwareToolAuthorizer:
    """Keep local policy unchanged and add qualified imported-tool authorization."""

    def __init__(
        self,
        *,
        local_authorizer: ToolAuthorizer,
        policy: MCPAccessPolicy,
        approval_repository: ApprovalRepositoryPort | None = None,
    ) -> None:
        self._local = local_authorizer
        self._policy = policy
        self._approval_repository = approval_repository

    def authorize_with_context(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        execution_context: ExecutionContext,
    ) -> None:
        if "." not in call.tool_name:
            self._local.authorize_with_context(call, definition, execution_context)
            return
        namespace, _, capability_name = call.tool_name.partition(".")
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
                "Security context does not match the MCP invocation",
                error_code="EXECUTION_CONTEXT_INVALID",
            )
        identity = MCPClientIdentity(
            client_id=execution_context.user_id,
            user_id=execution_context.user_id,
            tenant_id=execution_context.tenant_id,
            roles=execution_context.roles,
            scopes=execution_context.scopes,
            data_scope=execution_context.data_scope,
            purpose=execution_context.purpose,
            authentication_source=execution_context.authentication_source,
        )
        matching = tuple(rule for rule in self._policy.rules() if rule.namespace == namespace)
        if len(matching) != 1:
            raise ToolAuthorizationError(
                "MCP namespace is not bound to exactly one connection",
                error_code="MCP_ORIGIN_MISMATCH",
            )
        rule = matching[0]
        decision = self._policy.evaluate_capability(
            connection_id=rule.connection_id,
            server_id=rule.server_id,
            namespace=namespace,
            capability_name=capability_name,
            capability_type=MCPCapabilityType.TOOL,
            identity=identity,
        )
        if not decision.allowed:
            raise ToolAuthorizationError(decision.reason, error_code=decision.reason_code)
        if (decision.requires_approval or execution_context.approval_required) and (
            call.approval_id is None
        ):
            raise ToolAuthorizationError(
                "MCP invocation requires a bound approval", error_code="APPROVAL_REQUIRED"
            )
        if call.approval_id is not None:
            self._validate_approval(call, definition)

    def _validate_approval(self, call: ToolCall, definition: ToolDefinition) -> None:
        if self._approval_repository is None or call.approval_id is None:
            raise ToolAuthorizationError(
                "MCP approval validation is unavailable", error_code="APPROVAL_INVALID"
            )
        try:
            approval = self._approval_repository.get(call.approval_id, tenant_id=call.tenant_id)
        except KeyError as exc:
            raise ToolAuthorizationError(
                "MCP approval record was not found", error_code="APPROVAL_INVALID"
            ) from exc
        schema_digest = schema_fingerprint(definition)
        expected = action_fingerprint(
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
            or approval.expires_at <= utc_now()
            or approval.task_id != call.task_id
            or approval.tenant_id != call.tenant_id
            or approval.step_id != call.step_id
            or approval.tool_name != call.tool_name
            or approval.tool_version != call.tool_version
            or approval.input_schema_fingerprint != schema_digest
            or approval.resolved_arguments != call.input
            or approval.resolved_action_fingerprint != expected
        ):
            raise ToolAuthorizationError(
                "Approval does not cover this MCP invocation", error_code="APPROVAL_INVALID"
            )


def _deny(code: str, reason: str) -> MCPPolicyDecision:
    return MCPPolicyDecision(False, code, reason)


__all__ = [
    "MCPAccessPolicy",
    "MCPAccessRule",
    "MCPAwareToolAuthorizer",
    "MCPPolicyDecision",
]
