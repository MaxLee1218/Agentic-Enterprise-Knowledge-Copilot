"""Truthful cooperative cancellation primitives for tool invocations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import Event, RLock

from copilot.tools.exceptions import ToolCancellationError


class CancellationPhase(StrEnum):
    """Invocation lifecycle; requested is deliberately distinct from cancelled."""

    ACTIVE = "ACTIVE"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


class CancellationToken:
    """Thread-safe, idempotent cooperative cancellation signal."""

    def __init__(self) -> None:
        self._event = Event()
        self._phase = CancellationPhase.ACTIVE
        self._reason: str | None = None
        self._lock = RLock()

    @property
    def phase(self) -> CancellationPhase:
        with self._lock:
            return self._phase

    @property
    def reason(self) -> str | None:
        with self._lock:
            return self._reason

    @property
    def cancellation_requested(self) -> bool:
        return self._event.is_set()

    def request(self, reason: str = "Cancellation requested") -> bool:
        """Request cancellation once; never claim a running operation has stopped."""
        with self._lock:
            if self._phase in {CancellationPhase.CANCELLED, CancellationPhase.COMPLETED}:
                return False
            changed = self._phase is CancellationPhase.ACTIVE
            self._phase = CancellationPhase.CANCELLATION_REQUESTED
            self._reason = self._reason or reason
            self._event.set()
            return changed

    def raise_if_requested(self) -> None:
        """Cooperative interruption point used by cancellable adapters."""
        if self.cancellation_requested:
            self.mark_cancelled()
            raise ToolCancellationError(message=self.reason or "Tool invocation was cancelled")

    def mark_cancelled(self) -> None:
        with self._lock:
            if self._phase is not CancellationPhase.COMPLETED:
                self._phase = CancellationPhase.CANCELLED
                self._event.set()

    def mark_completed(self) -> bool:
        """Commit completion only if no cancellation won the race."""
        with self._lock:
            if self._phase is not CancellationPhase.ACTIVE:
                return False
            self._phase = CancellationPhase.COMPLETED
            return True


@dataclass(frozen=True, slots=True)
class ActiveInvocation:
    task_id: str
    tool_call_id: str
    token: CancellationToken


class InvocationCancellationRegistry:
    """Instance-scoped index used by task cancellation and graceful shutdown."""

    def __init__(self) -> None:
        self._active: dict[tuple[str, str], CancellationToken] = {}
        self._lock = RLock()

    def register(self, task_id: str, tool_call_id: str, token: CancellationToken) -> None:
        with self._lock:
            key = (task_id, tool_call_id)
            if key in self._active:
                raise ValueError("tool invocation is already active")
            self._active[key] = token

    def release(self, task_id: str, tool_call_id: str) -> None:
        with self._lock:
            self._active.pop((task_id, tool_call_id), None)

    def cancel_task(self, task_id: str, *, reason: str) -> int:
        with self._lock:
            tokens = tuple(
                token
                for (owner_task_id, _call_id), token in self._active.items()
                if owner_task_id == task_id
            )
        return sum(token.request(reason) for token in tokens)

    def cancel_all(self, *, reason: str) -> int:
        with self._lock:
            tokens = tuple(self._active.values())
        return sum(token.request(reason) for token in tokens)

    def snapshot(self) -> tuple[ActiveInvocation, ...]:
        with self._lock:
            return tuple(
                ActiveInvocation(task_id, call_id, token)
                for (task_id, call_id), token in sorted(self._active.items())
            )


__all__ = [
    "ActiveInvocation",
    "CancellationPhase",
    "CancellationToken",
    "InvocationCancellationRegistry",
]
