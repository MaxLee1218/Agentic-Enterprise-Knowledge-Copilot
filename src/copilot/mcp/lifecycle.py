"""Deterministic MCP session lifecycle with explicit legal transitions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from threading import RLock

from copilot.contracts import MCPSessionState
from copilot.contracts.validators import utc_now
from copilot.mcp.errors import MCPProtocolError, MCPSessionExpiredError

_TRANSITIONS: dict[MCPSessionState, frozenset[MCPSessionState]] = {
    MCPSessionState.CREATED: frozenset(
        {MCPSessionState.CONNECTING, MCPSessionState.CLOSED, MCPSessionState.FAILED}
    ),
    MCPSessionState.CONNECTING: frozenset(
        {MCPSessionState.INITIALIZING, MCPSessionState.FAILED, MCPSessionState.DISCONNECTING}
    ),
    MCPSessionState.INITIALIZING: frozenset(
        {MCPSessionState.NEGOTIATING, MCPSessionState.FAILED, MCPSessionState.DISCONNECTING}
    ),
    MCPSessionState.NEGOTIATING: frozenset(
        {MCPSessionState.READY, MCPSessionState.FAILED, MCPSessionState.DISCONNECTING}
    ),
    MCPSessionState.READY: frozenset(
        {
            MCPSessionState.DISCONNECTING,
            MCPSessionState.RECONNECTING,
            MCPSessionState.FAILED,
            MCPSessionState.EXPIRED,
        }
    ),
    MCPSessionState.RECONNECTING: frozenset(
        {MCPSessionState.CONNECTING, MCPSessionState.FAILED, MCPSessionState.EXPIRED}
    ),
    MCPSessionState.DISCONNECTING: frozenset({MCPSessionState.CLOSED, MCPSessionState.FAILED}),
    MCPSessionState.FAILED: frozenset(
        {MCPSessionState.RECONNECTING, MCPSessionState.DISCONNECTING, MCPSessionState.CLOSED}
    ),
    MCPSessionState.EXPIRED: frozenset({MCPSessionState.CLOSED}),
    MCPSessionState.CLOSED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class MCPLifecycleEvent:
    """One observable validated state transition."""

    previous: MCPSessionState
    current: MCPSessionState
    timestamp: datetime
    reason: str | None = None


class MCPSessionLifecycle:
    """Thread-safe state machine; protocol actions must consult it before I/O."""

    def __init__(
        self,
        *,
        initial: MCPSessionState = MCPSessionState.CREATED,
        clock: Callable[[], datetime] = utc_now,
        observer: Callable[[MCPLifecycleEvent], None] | None = None,
    ) -> None:
        self._state = initial
        self._clock = clock
        self._observer = observer
        self._events: list[MCPLifecycleEvent] = []
        self._lock = RLock()

    @property
    def state(self) -> MCPSessionState:
        with self._lock:
            return self._state

    @property
    def events(self) -> tuple[MCPLifecycleEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def transition(self, target: MCPSessionState, *, reason: str | None = None) -> None:
        """Commit one legal transition, or fail before changing state."""
        with self._lock:
            previous = self._state
            if target not in _TRANSITIONS[previous]:
                raise MCPProtocolError(
                    f"Illegal MCP lifecycle transition {previous.value} to {target.value}"
                )
            event = MCPLifecycleEvent(previous, target, self._clock(), reason)
            self._state = target
            self._events.append(event)
        if self._observer is not None:
            self._observer(event)

    def require_ready(self) -> None:
        """Reject invocation before initialization/negotiation or after closure."""
        state = self.state
        if state is MCPSessionState.EXPIRED:
            raise MCPSessionExpiredError("MCP session has expired")
        if state is not MCPSessionState.READY:
            raise MCPProtocolError(f"MCP session is not ready: {state.value}")


__all__ = ["MCPLifecycleEvent", "MCPSessionLifecycle"]
