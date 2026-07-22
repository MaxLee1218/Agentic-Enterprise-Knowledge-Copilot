"""Thread-safe in-memory persistence for deterministic workflow execution."""

from __future__ import annotations

from threading import RLock

from copilot.contracts import (
    StepResult,
    TaskContract,
    TaskPlan,
    TaskRequest,
    TaskResult,
    TaskState,
    ToolResult,
)
from copilot.services.workflows.models import StepExecutionRecord, TaskStateEvent


class InMemoryWorkflowRepository:
    """Append attempts/events and compare-and-swap TaskState snapshots in memory."""

    def __init__(self) -> None:
        self._requests: dict[str, TaskRequest] = {}
        self._contracts: dict[str, TaskContract] = {}
        self._plans: dict[str, TaskPlan] = {}
        self._states: dict[str, TaskState] = {}
        self._state_events: list[TaskStateEvent] = []
        self._tool_results: list[ToolResult] = []
        self._step_results: dict[str, StepResult] = {}
        self._step_executions: dict[str, StepExecutionRecord] = {}
        self._task_results: dict[str, TaskResult] = {}
        self._lock = RLock()

    def initialize(
        self,
        request: TaskRequest,
        contract: TaskContract,
        plan: TaskPlan,
        state: TaskState,
    ) -> None:
        """Persist initial values exactly once per task."""
        with self._lock:
            if contract.task_id in self._states:
                raise ValueError("workflow task already exists")
            self._requests[contract.task_id] = request
            self._contracts[contract.task_id] = contract
            self._plans[contract.task_id] = plan
            self._states[contract.task_id] = state

    def commit_transition(
        self,
        previous: TaskState,
        current: TaskState,
        event: TaskStateEvent,
    ) -> None:
        """Atomically compare state version, append event, and replace the snapshot."""
        with self._lock:
            authoritative = self._states.get(previous.task_id)
            if authoritative != previous or current.version != previous.version + 1:
                raise ValueError("task state compare-and-swap conflict")
            if event.event_id != current.last_event_id:
                raise ValueError("state event does not produce the supplied snapshot")
            self._state_events.append(event)
            self._states[current.task_id] = current

    def save_tool_result(self, result: ToolResult) -> None:
        """Append one unique tool attempt."""
        with self._lock:
            if any(item.tool_call_id == result.tool_call_id for item in self._tool_results):
                raise ValueError("tool result already exists")
            self._tool_results.append(result)

    def save_step_result(self, result: StepResult, execution: StepExecutionRecord) -> None:
        """Save exactly one final result per planned step."""
        with self._lock:
            if result.step_id in self._step_results:
                raise ValueError("step result already exists")
            self._step_results[result.step_id] = result
            self._step_executions[result.step_id] = execution

    def save_task_result(self, result: TaskResult) -> None:
        """Save exactly one terminal result per task."""
        with self._lock:
            if result.task_id in self._task_results:
                raise ValueError("task result already exists")
            self._task_results[result.task_id] = result

    def state_for(self, task_id: str) -> TaskState:
        """Return the authoritative state snapshot."""
        with self._lock:
            return self._states[task_id]

    def step_results(self) -> tuple[StepResult, ...]:
        """Return step results in persistence order."""
        with self._lock:
            return tuple(self._step_results.values())

    def tool_results(self) -> tuple[ToolResult, ...]:
        """Return every attempt in append order."""
        with self._lock:
            return tuple(self._tool_results)

    def state_events(self) -> tuple[TaskStateEvent, ...]:
        """Return immutable state events in transition order."""
        with self._lock:
            return tuple(self._state_events)
