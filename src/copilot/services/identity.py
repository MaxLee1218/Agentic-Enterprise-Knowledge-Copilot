"""Framework-independent authenticated identity boundary.

Transport adapters must resolve an identity through this port before invoking an
application service.  User supplied task text and metadata are never identity sources.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from copilot.services.task_intake import TrustedCallerContext


class IdentityResolutionError(RuntimeError):
    """Safe authentication failure exposed by transport adapters."""

    code = "AUTHENTICATION_REQUIRED"


@dataclass(frozen=True, slots=True)
class IdentityRequest:
    """Minimal transport-neutral input accepted by an identity provider."""

    headers: Mapping[str, str]
    source: str


class IdentityProvider(Protocol):
    """Resolve authenticated, server-trusted caller facts or fail closed."""

    def resolve(self, request: IdentityRequest) -> TrustedCallerContext:
        """Return an authenticated identity without consulting task payloads."""
        ...


__all__ = ["IdentityProvider", "IdentityRequest", "IdentityResolutionError"]
