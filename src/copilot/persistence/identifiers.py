"""Identifier factories for local composition and deterministic tests."""

from __future__ import annotations

from collections import defaultdict
from threading import RLock
from uuid import uuid4


class UuidIdentifierFactory:
    """Create globally unique opaque identifiers."""

    def new_id(self, prefix: str) -> str:
        """Return a UUID-backed identifier with a safe semantic prefix."""
        return f"{prefix}-{uuid4().hex}"


class SequentialIdentifierFactory:
    """Create deterministic unique identifiers for controlled tests."""

    def __init__(self) -> None:
        self._counts: defaultdict[str, int] = defaultdict(int)
        self._lock = RLock()

    def new_id(self, prefix: str) -> str:
        """Return the next per-prefix identifier."""
        with self._lock:
            self._counts[prefix] += 1
            return f"{prefix}-{self._counts[prefix]:04d}"
