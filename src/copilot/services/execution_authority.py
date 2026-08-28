"""Application-owned execution-authority context shared with fenced adapters."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from copilot.contracts.async_runtime import ExecutionLease

_AUTHORITY: ContextVar[ExecutionLease | None] = ContextVar(
    "copilot_worker_execution_authority",
    default=None,
)


@contextmanager
def bind_execution_authority(lease: ExecutionLease) -> Iterator[None]:
    """Bind one lease to authoritative mutations in the current execution context."""
    token: Token[ExecutionLease | None] = _AUTHORITY.set(lease)
    try:
        yield
    finally:
        _AUTHORITY.reset(token)


def current_execution_authority() -> ExecutionLease | None:
    """Return the current Worker lease, or ``None`` outside Worker execution."""
    return _AUTHORITY.get()


__all__ = ["bind_execution_authority", "current_execution_authority"]
