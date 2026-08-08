"""Concrete development and signed trusted-gateway identity providers."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable, Mapping

from copilot.config import Settings
from copilot.services.identity import IdentityRequest, IdentityResolutionError
from copilot.services.task_intake import TrustedCallerContext

_USER = "x-copilot-user-id"
_TENANT = "x-copilot-tenant-id"
_ROLES = "x-copilot-roles"
_SCOPES = "x-copilot-scopes"
_SUPPLIERS = "x-copilot-supplier-ids"
_PURPOSE = "x-copilot-purpose"
_TIMESTAMP = "x-copilot-identity-timestamp"
_SIGNATURE = "x-copilot-identity-signature"


class DemoIdentityProvider:
    """Explicit local-only identity provider; construction fails in production."""

    def __init__(self, settings: Settings) -> None:
        if settings.app_env == "production":
            raise IdentityResolutionError("Demo identity is forbidden in production")
        self._settings = settings

    def resolve(self, request: IdentityRequest) -> TrustedCallerContext:
        del request
        return TrustedCallerContext(
            user_id=self._settings.demo_user_id,
            tenant_id=self._settings.demo_tenant_id,
            data_scope=("quality.v1", "supplier-quality-policy-v1"),
            scopes=("task:read", "task:execute", "task:cancel", "evidence:read"),
            roles=self._settings.demo_approval_roles,
            authentication_source="configured_demo_identity_provider",
            authenticated=True,
            is_demo_identity=True,
            purpose="supplier_quality_analysis.v1",
            policy_forces_read_only=self._settings.task_force_read_only,
            policy_requires_approval=self._settings.task_require_approval_by_default,
        )


class TrustedHeaderIdentityProvider:
    """Verify a short-lived HMAC assertion emitted by an approved edge gateway.

    The gateway-authenticated user, tenant, roles, scopes, suppliers, and purpose are all
    covered by one signature.  Missing, stale, malformed, or altered assertions are denied.
    """

    def __init__(
        self,
        secret: str,
        *,
        max_age_seconds: int = 60,
        clock: Callable[[], float] = time.time,
        force_read_only: bool = True,
        require_approval: bool = False,
    ) -> None:
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("Identity signing secret must contain at least 32 bytes")
        if max_age_seconds < 1:
            raise ValueError("Identity assertion max age must be positive")
        self._secret = secret.encode("utf-8")
        self._max_age_seconds = max_age_seconds
        self._clock = clock
        self._force_read_only = force_read_only
        self._require_approval = require_approval

    def resolve(self, request: IdentityRequest) -> TrustedCallerContext:
        headers = {key.lower(): value.strip() for key, value in request.headers.items()}
        required = (_USER, _TENANT, _ROLES, _SCOPES, _PURPOSE, _TIMESTAMP, _SIGNATURE)
        if any(not headers.get(name) for name in required):
            raise IdentityResolutionError("A signed identity assertion is required")
        try:
            issued_at = int(headers[_TIMESTAMP])
        except ValueError as exc:
            raise IdentityResolutionError("Identity assertion timestamp is invalid") from exc
        age = self._clock() - issued_at
        if age < -5 or age > self._max_age_seconds:
            raise IdentityResolutionError("Identity assertion is outside its validity window")
        canonical = self.canonical_assertion(headers)
        expected = hmac.new(self._secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        supplied = headers[_SIGNATURE].removeprefix("sha256=")
        if len(supplied) != 64 or not hmac.compare_digest(expected, supplied):
            raise IdentityResolutionError("Identity assertion signature is invalid")
        roles = _csv(headers[_ROLES])
        scopes = _csv(headers[_SCOPES])
        if not roles or not scopes:
            raise IdentityResolutionError("Authenticated roles and scopes are required")
        data_scope = tuple(
            scope.removeprefix("data:") for scope in scopes if scope.startswith("data:")
        )
        if not data_scope:
            raise IdentityResolutionError("At least one authenticated data scope is required")
        return TrustedCallerContext(
            user_id=headers[_USER],
            tenant_id=headers[_TENANT],
            data_scope=data_scope,
            supplier_ids=_csv(headers.get(_SUPPLIERS, "")),
            roles=roles,
            scopes=scopes,
            authentication_source=f"trusted_gateway_hmac:{request.source}",
            authenticated=True,
            is_demo_identity=False,
            purpose=headers[_PURPOSE],
            policy_forces_read_only=self._force_read_only,
            policy_requires_approval=self._require_approval,
        )

    @staticmethod
    def canonical_assertion(headers: Mapping[str, str]) -> str:
        """Return the exact signed representation, excluding the signature itself."""
        normalized = {key.lower(): value.strip() for key, value in headers.items()}
        return "\n".join(
            f"{name}:{normalized.get(name, '')}"
            for name in (_USER, _TENANT, _ROLES, _SCOPES, _SUPPLIERS, _PURPOSE, _TIMESTAMP)
        )


def build_identity_provider(
    settings: Settings,
) -> DemoIdentityProvider | TrustedHeaderIdentityProvider:
    """Compose the configured identity boundary with no fallback behavior."""
    if settings.identity_provider == "demo":
        return DemoIdentityProvider(settings)
    secret = settings.require_identity_signing_secret().get_secret_value()
    return TrustedHeaderIdentityProvider(
        secret,
        max_age_seconds=settings.identity_assertion_max_age_seconds,
        force_read_only=settings.task_force_read_only,
        require_approval=settings.task_require_approval_by_default,
    )


def _csv(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))


__all__ = [
    "DemoIdentityProvider",
    "TrustedHeaderIdentityProvider",
    "build_identity_provider",
]
