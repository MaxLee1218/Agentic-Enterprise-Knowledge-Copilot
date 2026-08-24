"""Central demo permission matrix and stable deny-by-default policy decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from copilot.contracts import CapabilityName


class Permission(StrEnum):
    """Actions governed by the currently executable use-case boundaries."""

    SUBMIT_TASK = "submit_task"
    EXECUTE_TOOL = "execute_tool"
    READ_TASK = "read_task"
    READ_EVIDENCE = "read_evidence"
    READ_ARTIFACT = "read_artifact"
    DOWNLOAD_ARTIFACT = "download_artifact"
    GENERATE_REPORT = "generate_report"
    APPROVE_ACTION = "approve_action"
    CANCEL_TASK = "cancel_task"


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """Minimized deterministic authorization input without user-controlled authority fields."""

    action: Permission
    roles: tuple[str, ...]
    resource_type: str
    resource_name: str = ""
    tool_name: str = ""
    task_id: str = ""
    purpose: str = "supplier_quality_analysis.v1"
    scopes: tuple[str, ...] = ()
    is_demo_identity: bool = False


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Stable allow/deny result suitable for audit and tests."""

    allowed: bool
    reason_code: str
    reason: str
    required_permissions: tuple[Permission, ...]
    matched_rules: tuple[str, ...]
    requires_approval: bool = False


_FROZEN_TOOLS = frozenset(item.value for item in CapabilityName)
_ROLE_PERMISSIONS = {
    "quality_analyst": frozenset(
        {
            Permission.SUBMIT_TASK,
            Permission.EXECUTE_TOOL,
            Permission.READ_TASK,
            Permission.READ_EVIDENCE,
            Permission.READ_ARTIFACT,
            Permission.DOWNLOAD_ARTIFACT,
            Permission.GENERATE_REPORT,
            Permission.CANCEL_TASK,
        }
    ),
    "quality_data_approver": frozenset(Permission),
    "finance_analyst": frozenset(
        {
            Permission.SUBMIT_TASK,
            Permission.EXECUTE_TOOL,
            Permission.READ_TASK,
            Permission.READ_EVIDENCE,
            Permission.READ_ARTIFACT,
            Permission.DOWNLOAD_ARTIFACT,
            Permission.GENERATE_REPORT,
            Permission.CANCEL_TASK,
        }
    ),
    "finance_approver": frozenset(Permission),
    "finance_auditor": frozenset(
        {
            Permission.READ_TASK,
            Permission.READ_EVIDENCE,
            Permission.READ_ARTIFACT,
            Permission.DOWNLOAD_ARTIFACT,
        }
    ),
}

_PURPOSE_ROLES = {
    "supplier_quality_analysis.v1": {"quality_analyst", "quality_data_approver"},
    "accounts_payable_analysis.v1": {
        "finance_analyst",
        "finance_approver",
        "finance_auditor",
    },
}


class PermissionMatrix:
    """Evaluate centralized role/action/tool rules; unknown values never imply access."""

    def effective_roles(
        self,
        roles: tuple[str, ...],
        *,
        is_demo_identity: bool,
        purpose: str = "supplier_quality_analysis.v1",
    ) -> tuple[str, ...]:
        """Apply the documented demo-only least-privilege fallback."""
        if roles:
            return tuple(dict.fromkeys(roles))
        if not is_demo_identity:
            return ()
        return (
            ("finance_analyst",)
            if purpose == "accounts_payable_analysis.v1"
            else ("quality_analyst",)
        )

    def evaluate(self, request: AuthorizationRequest) -> PolicyDecision:
        """Return an explicit reason-coded decision for one requested action."""
        roles = self.effective_roles(
            request.roles,
            is_demo_identity=request.is_demo_identity,
            purpose=request.purpose,
        )
        if not roles:
            return _deny("UNKNOWN_ROLE", "No authorized role is available", request.action)
        unknown = tuple(role for role in roles if role not in _ROLE_PERMISSIONS)
        if unknown:
            return _deny("UNKNOWN_ROLE", "Caller role is not recognized", request.action)
        if request.purpose not in _PURPOSE_ROLES:
            return _deny(
                "V1_1_CAPABILITY_NOT_ALLOWED",
                "Requested purpose is outside the governed task profiles",
                request.action,
            )
        purpose_roles = tuple(role for role in roles if role in _PURPOSE_ROLES[request.purpose])
        if not purpose_roles:
            return _deny(
                "UNKNOWN_ROLE",
                "Caller role is not authorized for the selected purpose",
                request.action,
            )
        if request.action is Permission.EXECUTE_TOOL:
            if request.tool_name not in _FROZEN_TOOLS:
                return _deny(
                    "TOOL_NOT_ALLOWED",
                    "Tool is outside the frozen v1.1 allowlist",
                    request.action,
                )
            if request.tool_name == CapabilityName.REPORT_GENERATOR.value and not any(
                Permission.GENERATE_REPORT in _ROLE_PERMISSIONS[role] for role in purpose_roles
            ):
                return _deny(
                    "TOOL_NOT_ALLOWED", "Report generation is not permitted", request.action
                )
        if not any(request.action in _ROLE_PERMISSIONS[role] for role in purpose_roles):
            code = (
                "APPROVAL_PERMISSION_DENIED"
                if request.action is Permission.APPROVE_ACTION
                else "PERMISSION_DENIED"
            )
            return _deny(code, "Caller lacks the required permission", request.action)
        if (
            request.action is Permission.DOWNLOAD_ARTIFACT
            and request.purpose == "accounts_payable_analysis.v1"
            and "finance:ap.artifact:download" not in request.scopes
        ):
            return _deny(
                "ARTIFACT_DOWNLOAD_SCOPE_REQUIRED",
                "Accounts Payable Artifact download requires an explicit scope",
                request.action,
            )
        return PolicyDecision(
            allowed=True,
            reason_code="ALLOWED",
            reason="Permission is explicitly allowed by the demo role matrix",
            required_permissions=(request.action,),
            matched_rules=tuple(f"role:{role}" for role in purpose_roles),
        )


def _deny(code: str, reason: str, permission: Permission) -> PolicyDecision:
    return PolicyDecision(
        allowed=False,
        reason_code=code,
        reason=reason,
        required_permissions=(permission,),
        matched_rules=("deny_by_default",),
    )


__all__ = [
    "AuthorizationRequest",
    "Permission",
    "PermissionMatrix",
    "PolicyDecision",
]
