"""Application-owned liveness/readiness semantics."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

DependencyState = Literal["ok", "unavailable", "not_configured"]


@dataclass(frozen=True, slots=True)
class ReadinessSnapshot:
    """Safe dependency status without exception messages or connection details."""

    status: Literal["ready", "degraded", "not_ready"]
    accepts_tasks: bool
    dependencies: Mapping[str, DependencyState]


class ReadinessService:
    """Evaluate bounded injected probes without exposing infrastructure objects to FastAPI."""

    def __init__(
        self,
        probes: Mapping[str, Callable[[], object] | None],
        *,
        task_dependencies: frozenset[str],
    ) -> None:
        self._probes = dict(probes)
        self._task_dependencies = task_dependencies

    def check(self) -> ReadinessSnapshot:
        """Return task-acceptance status while keeping history-read semantics explicit."""
        dependencies: dict[str, DependencyState] = {}
        for name, probe in self._probes.items():
            if probe is None:
                dependencies[name] = "not_configured"
                continue
            try:
                dependencies[name] = "unavailable" if probe() is False else "ok"
            except Exception:
                dependencies[name] = "unavailable"
        persistence_ready = dependencies.get("database") == "ok"
        accepts_tasks = persistence_ready and all(
            dependencies.get(name) == "ok" for name in self._task_dependencies
        )
        if not persistence_ready:
            status: Literal["ready", "degraded", "not_ready"] = "not_ready"
        elif accepts_tasks:
            status = "ready"
        else:
            status = "degraded"
        return ReadinessSnapshot(
            status=status,
            accepts_tasks=accepts_tasks,
            dependencies=dependencies,
        )


__all__ = ["DependencyState", "ReadinessService", "ReadinessSnapshot"]
