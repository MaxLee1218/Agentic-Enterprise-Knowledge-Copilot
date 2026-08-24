"""Concrete development and signed trusted-gateway identity providers."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from copilot.config import Settings
from copilot.contracts import MoneyThreshold, TaskType
from copilot.services.identity import IdentityRequest, IdentityResolutionError
from copilot.services.task_intake import TrustedCallerContext

_USER = "x-copilot-user-id"
_TENANT = "x-copilot-tenant-id"
_ROLES = "x-copilot-roles"
_SCOPES = "x-copilot-scopes"
_SUPPLIERS = "x-copilot-supplier-ids"
_LEGAL_ENTITIES = "x-copilot-legal-entity-ids"
_BUSINESS_UNITS = "x-copilot-business-unit-ids"
_CURRENCIES = "x-copilot-currency-scope"
_ASSIGNED_TASKS = "x-copilot-assigned-task-ids"
_ALLOWED_TASK_TYPES = "x-copilot-allowed-task-types"
_PURPOSE = "x-copilot-purpose"
_POLICY_RULE_SET_ID = "x-copilot-policy-rule-set-id"
_POLICY_RULE_SET_VERSION = "x-copilot-policy-rule-set-version"
_POLICY_MANIFEST_CHECKSUM = "x-copilot-policy-manifest-checksum"
_POLICY_MATERIALITY = "x-copilot-policy-materiality"
_POLICY_SNAPSHOT_AT = "x-copilot-policy-snapshot-at"
_POLICY_REQUIRES_APPROVAL = "x-copilot-policy-requires-approval"
_TIMESTAMP = "x-copilot-identity-timestamp"
_SIGNATURE = "x-copilot-identity-signature"

_SIGNED_HEADERS = (
    _USER,
    _TENANT,
    _ROLES,
    _SCOPES,
    _SUPPLIERS,
    _LEGAL_ENTITIES,
    _BUSINESS_UNITS,
    _CURRENCIES,
    _ASSIGNED_TASKS,
    _ALLOWED_TASK_TYPES,
    _PURPOSE,
    _POLICY_RULE_SET_ID,
    _POLICY_RULE_SET_VERSION,
    _POLICY_MANIFEST_CHECKSUM,
    _POLICY_MATERIALITY,
    _POLICY_SNAPSHOT_AT,
    _POLICY_REQUIRES_APPROVAL,
    _TIMESTAMP,
)


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
        purpose = _task_type(headers[_PURPOSE])
        allowed_task_types = tuple(
            _task_type(value)
            for value in (_csv(headers.get(_ALLOWED_TASK_TYPES, "")) or (purpose.value,))
        )
        try:
            return TrustedCallerContext(
                user_id=headers[_USER],
                tenant_id=headers[_TENANT],
                data_scope=data_scope,
                supplier_ids=_csv(headers.get(_SUPPLIERS, "")),
                legal_entity_ids=_csv(headers.get(_LEGAL_ENTITIES, "")),
                business_unit_ids=_csv(headers.get(_BUSINESS_UNITS, "")),
                currency_scope=_csv(headers.get(_CURRENCIES, "")),
                assigned_task_ids=_csv(headers.get(_ASSIGNED_TASKS, "")),
                allowed_task_types=allowed_task_types,
                roles=roles,
                scopes=scopes,
                authentication_source=f"trusted_gateway_hmac:{request.source}",
                authenticated=True,
                is_demo_identity=False,
                purpose=purpose.value,
                policy_rule_set_id=headers.get(_POLICY_RULE_SET_ID) or None,
                policy_rule_set_version=headers.get(_POLICY_RULE_SET_VERSION) or None,
                policy_manifest_checksum=headers.get(_POLICY_MANIFEST_CHECKSUM) or None,
                policy_materiality=_money_thresholds(headers.get(_POLICY_MATERIALITY, "")),
                policy_snapshot_at=_optional_datetime(headers.get(_POLICY_SNAPSHOT_AT, "")),
                policy_forces_read_only=self._force_read_only,
                policy_requires_approval=(
                    self._require_approval
                    or _optional_bool(headers.get(_POLICY_REQUIRES_APPROVAL, ""))
                ),
            )
        except ValidationError as exc:
            raise IdentityResolutionError("Identity assertion contains invalid authority") from exc

    @staticmethod
    def canonical_assertion(headers: Mapping[str, str]) -> str:
        """Return the exact signed representation, excluding the signature itself."""
        normalized = {key.lower(): value.strip() for key, value in headers.items()}
        return "\n".join(f"{name}:{normalized.get(name, '')}" for name in _SIGNED_HEADERS)


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


def _task_type(value: str) -> TaskType:
    try:
        return TaskType(value)
    except ValueError as exc:
        raise IdentityResolutionError("Identity assertion task type is invalid") from exc


def _money_thresholds(value: str) -> tuple[MoneyThreshold, ...]:
    if not value.strip():
        return ()
    thresholds: list[MoneyThreshold] = []
    try:
        for item in _csv(value):
            currency, amount = item.split("=", maxsplit=1)
            thresholds.append(
                MoneyThreshold(currency=currency.strip(), amount=Decimal(amount.strip()))
            )
    except (InvalidOperation, ValidationError, ValueError) as exc:
        raise IdentityResolutionError("Identity policy materiality is invalid") from exc
    return tuple(thresholds)


def _optional_datetime(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IdentityResolutionError("Identity policy snapshot timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise IdentityResolutionError("Identity policy snapshot timestamp must include a timezone")
    return parsed


def _optional_bool(value: str) -> bool:
    if not value.strip():
        return False
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise IdentityResolutionError("Identity approval flag is invalid")
    return normalized == "true"


__all__ = [
    "DemoIdentityProvider",
    "TrustedHeaderIdentityProvider",
    "build_identity_provider",
]
