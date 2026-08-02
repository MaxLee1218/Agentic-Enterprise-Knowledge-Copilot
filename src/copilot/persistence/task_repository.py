"""Thread-safe in-memory persistence for deterministic workflow execution."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from threading import RLock

from copilot.contracts import (
    JsonObject,
    StepResult,
    TaskContract,
    TaskPlan,
    TaskRequest,
    TaskResult,
    TaskState,
    ToolResult,
    VerificationResult,
)
from copilot.services.workflows.models import StepExecutionRecord, TaskStateEvent


class InMemoryWorkflowRepository:
    """Append attempts/events and compare-and-swap TaskState snapshots in memory."""

    def __init__(self, database_path: Path | None = None) -> None:
        self._requests: dict[str, TaskRequest] = {}
        self._contracts: dict[str, TaskContract] = {}
        self._plans: dict[str, TaskPlan] = {}
        self._states: dict[str, TaskState] = {}
        self._state_events: list[TaskStateEvent] = []
        self._tool_results: list[ToolResult] = []
        self._step_results: dict[str, StepResult] = {}
        self._step_executions: dict[str, StepExecutionRecord] = {}
        self._task_results: dict[str, TaskResult] = {}
        self._verification_results: dict[str, VerificationResult] = {}
        self._execution_leases: dict[str, str] = {}
        self._lock = RLock()
        self._database = (
            sqlite3.connect(database_path, check_same_thread=False)
            if database_path is not None
            else None
        )
        if self._database is not None:
            self._setup()
            self._load()

    def initialize(
        self,
        request: TaskRequest,
        contract: TaskContract | None,
        plan: TaskPlan | None,
        state: TaskState,
        *,
        task_id: str | None = None,
    ) -> None:
        """Persist initial values exactly once per task."""
        task_id = task_id or (contract.task_id if contract is not None else state.task_id)
        with self._lock:
            if task_id in self._states:
                raise ValueError("workflow task already exists")
            if self._database is not None:
                with self._database:
                    self._database.execute(
                        """
                        INSERT INTO workflow_tasks
                        (task_id, request_json, contract_json, plan_json, state_json)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            task_id,
                            request.model_dump_json(),
                            contract.model_dump_json() if contract is not None else "null",
                            plan.model_dump_json() if plan is not None else "null",
                            state.model_dump_json(),
                        ),
                    )
            self._requests[task_id] = request
            if contract is not None:
                self._contracts[task_id] = contract
            if plan is not None:
                self._plans[task_id] = plan
            self._states[task_id] = state

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
            if self._database is not None:
                with self._database:
                    updated = self._database.execute(
                        """
                        UPDATE workflow_tasks
                        SET state_json = ?
                        WHERE task_id = ? AND state_json = ?
                        """,
                        (
                            current.model_dump_json(),
                            current.task_id,
                            previous.model_dump_json(),
                        ),
                    )
                    if updated.rowcount != 1:
                        raise ValueError("task state compare-and-swap conflict")
                    self._database.execute(
                        "INSERT INTO workflow_state_events VALUES (?, ?, ?)",
                        (event.event_id, event.task_id, _event_json(event)),
                    )
            self._state_events.append(event)
            self._states[current.task_id] = current

    def save_contract(self, contract: TaskContract) -> None:
        """Persist the understanding result before any business tool execution."""
        with self._lock:
            if contract.task_id not in self._states:
                raise ValueError("workflow task was not initialized")
            if self._tool_results:
                existing_task_results = [
                    result for result in self._tool_results if result.task_id == contract.task_id
                ]
                if existing_task_results:
                    raise ValueError("contract cannot change after tool execution")
            existing = self._contracts.get(contract.task_id)
            if existing is not None and contract.contract_version < existing.contract_version:
                raise ValueError("contract version cannot decrease")
            if self._database is not None:
                with self._database:
                    self._database.execute(
                        "UPDATE workflow_tasks SET contract_json = ? WHERE task_id = ?",
                        (contract.model_dump_json(), contract.task_id),
                    )
            self._contracts[contract.task_id] = contract

    def save_plan(self, plan: TaskPlan) -> None:
        """Persist an LLM candidate as the current plan before tool execution."""
        with self._lock:
            if plan.task_id not in self._states:
                raise ValueError("workflow task was not initialized")
            existing = self._plans.get(plan.task_id)
            if existing is not None and plan == existing:
                return
            if any(result.task_id == plan.task_id for result in self._tool_results):
                if existing is not None and plan.planning_version <= existing.planning_version:
                    raise ValueError("replan version must increase after tool execution")
            elif existing is not None and plan.planning_version < existing.planning_version:
                raise ValueError("plan version cannot decrease")
            if self._database is not None:
                with self._database:
                    if existing is not None:
                        self._database.execute(
                            """
                            INSERT OR IGNORE INTO workflow_plan_history
                            (task_id, planning_version, plan_json)
                            VALUES (?, ?, ?)
                            """,
                            (
                                existing.task_id,
                                existing.planning_version,
                                existing.model_dump_json(),
                            ),
                        )
                    self._database.execute(
                        "UPDATE workflow_tasks SET plan_json = ? WHERE task_id = ?",
                        (plan.model_dump_json(), plan.task_id),
                    )
            self._plans[plan.task_id] = plan

    def save_tool_result(self, result: ToolResult) -> None:
        """Append one unique tool attempt."""
        with self._lock:
            existing = next(
                (item for item in self._tool_results if item.tool_call_id == result.tool_call_id),
                None,
            )
            if existing is not None and existing == result:
                return
                raise ValueError("tool result already exists")
            if self._database is not None:
                with self._database:
                    self._database.execute(
                        "INSERT INTO workflow_tool_results VALUES (?, ?, ?)",
                        (result.tool_call_id, result.task_id, result.model_dump_json()),
                    )
            self._tool_results.append(result)

    def save_step_result(self, result: StepResult, execution: StepExecutionRecord) -> None:
        """Save exactly one final result per planned step."""
        with self._lock:
            if result.step_id in self._step_results:
                if (
                    self._step_results[result.step_id] == result
                    and self._step_executions[result.step_id] == execution
                ):
                    return
                raise ValueError("step result already exists")
            if self._database is not None:
                task_id = _task_id_for_step(self._plans, result.step_id)
                with self._database:
                    self._database.execute(
                        "INSERT INTO workflow_step_results VALUES (?, ?, ?, ?)",
                        (
                            result.step_id,
                            task_id,
                            result.model_dump_json(),
                            _execution_json(execution),
                        ),
                    )
            self._step_results[result.step_id] = result
            self._step_executions[result.step_id] = execution

    def save_task_result(self, result: TaskResult) -> None:
        """Save exactly one terminal result per task."""
        with self._lock:
            if result.task_id in self._task_results:
                if self._task_results[result.task_id] == result:
                    return
                raise ValueError("task result already exists")
            if self._database is not None:
                with self._database:
                    self._database.execute(
                        "UPDATE workflow_tasks SET task_result_json = ? WHERE task_id = ?",
                        (result.model_dump_json(), result.task_id),
                    )
            self._task_results[result.task_id] = result

    def save_verification_result(self, result: VerificationResult) -> None:
        """Append a verification attempt and retain the latest result for recovery."""
        with self._lock:
            existing = self._verification_results.get(result.task_id)
            if existing is not None and existing == result:
                return
            if self._database is not None:
                with self._database:
                    if existing is not None:
                        self._database.execute(
                            """
                            INSERT OR IGNORE INTO workflow_verification_history
                            (task_id, verification_json)
                            VALUES (?, ?)
                            """,
                            (existing.task_id, existing.model_dump_json()),
                        )
                    self._database.execute(
                        "UPDATE workflow_tasks SET verification_json = ? WHERE task_id = ?",
                        (result.model_dump_json(), result.task_id),
                    )
            self._verification_results[result.task_id] = result

    def acquire_execution(self, task_id: str, owner_id: str) -> None:
        """Prevent concurrent start/resume for one task."""
        with self._lock:
            if self._database is not None:
                if task_id in self._task_results:
                    raise ValueError("terminal task cannot be resumed")
                expires_at = time.time() + 600
                self._database.execute(
                    "DELETE FROM workflow_leases WHERE expires_at <= ?",
                    (time.time(),),
                )
                try:
                    self._database.execute(
                        "INSERT INTO workflow_leases VALUES (?, ?, ?)",
                        (task_id, owner_id, expires_at),
                    )
                except sqlite3.IntegrityError as exc:
                    self._database.rollback()
                    raise ValueError("task execution lease conflict") from exc
                self._database.commit()
            owner = self._execution_leases.get(task_id)
            if owner is not None and owner != owner_id:
                raise ValueError("task execution lease conflict")
            if task_id in self._task_results:
                raise ValueError("terminal task cannot be resumed")
            self._execution_leases[task_id] = owner_id

    def release_execution(self, task_id: str, owner_id: str) -> None:
        """Release only the caller's task lease."""
        with self._lock:
            if self._execution_leases.get(task_id) == owner_id:
                del self._execution_leases[task_id]
            if self._database is not None:
                self._database.execute(
                    "DELETE FROM workflow_leases WHERE task_id = ? AND owner_id = ?",
                    (task_id, owner_id),
                )
                self._database.commit()

    def state_for(self, task_id: str) -> TaskState:
        """Return the authoritative state snapshot."""
        with self._lock:
            return self._states[task_id]

    def request_for(self, task_id: str) -> TaskRequest:
        """Return the immutable original request."""
        with self._lock:
            return self._requests[task_id]

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

    def verification_result_for(self, task_id: str) -> VerificationResult:
        """Return the persisted deterministic verification result."""
        with self._lock:
            return self._verification_results[task_id]

    def close(self) -> None:
        """Close the optional durable SQLite connection."""
        with self._lock:
            if self._database is not None:
                self._database.close()
                self._database = None

    def _setup(self) -> None:
        assert self._database is not None
        self._database.executescript(
            """
            CREATE TABLE IF NOT EXISTS workflow_tasks (
                task_id TEXT PRIMARY KEY,
                request_json TEXT NOT NULL,
                contract_json TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                state_json TEXT NOT NULL,
                task_result_json TEXT,
                verification_json TEXT
            );
            CREATE TABLE IF NOT EXISTS workflow_state_events (
                event_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workflow_tool_results (
                tool_call_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workflow_step_results (
                step_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                result_json TEXT NOT NULL,
                execution_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workflow_leases (
                task_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workflow_plan_history (
                task_id TEXT NOT NULL,
                planning_version INTEGER NOT NULL,
                plan_json TEXT NOT NULL,
                UNIQUE(task_id, planning_version, plan_json)
            );
            CREATE TABLE IF NOT EXISTS workflow_verification_history (
                task_id TEXT NOT NULL,
                verification_json TEXT NOT NULL,
                UNIQUE(task_id, verification_json)
            );
            """
        )
        self._database.commit()

    def _load(self) -> None:
        assert self._database is not None
        for row in self._database.execute(
            """
            SELECT task_id, request_json, contract_json, plan_json, state_json,
                   task_result_json, verification_json
            FROM workflow_tasks
            """
        ):
            task_id = str(row[0])
            self._requests[task_id] = TaskRequest.model_validate_json(row[1])
            if row[2] != "null":
                self._contracts[task_id] = TaskContract.model_validate_json(row[2])
            if row[3] != "null":
                self._plans[task_id] = TaskPlan.model_validate_json(row[3])
            self._states[task_id] = TaskState.model_validate_json(row[4])
            if row[5] is not None:
                self._task_results[task_id] = TaskResult.model_validate_json(row[5])
            if row[6] is not None:
                self._verification_results[task_id] = VerificationResult.model_validate_json(row[6])
        for row in self._database.execute(
            "SELECT payload_json FROM workflow_tool_results ORDER BY rowid"
        ):
            self._tool_results.append(ToolResult.model_validate_json(row[0]))
        for row in self._database.execute(
            "SELECT result_json, execution_json FROM workflow_step_results ORDER BY rowid"
        ):
            result = StepResult.model_validate_json(row[0])
            self._step_results[result.step_id] = result
            self._step_executions[result.step_id] = _execution_from_json(row[1])
        for row in self._database.execute(
            "SELECT payload_json FROM workflow_state_events ORDER BY rowid"
        ):
            self._state_events.append(_event_from_json(row[0]))


def _task_id_for_step(plans: dict[str, TaskPlan], step_id: str) -> str:
    return next(
        task_id
        for task_id, plan in plans.items()
        if any(step.step_id == step_id for step in plan.steps)
    )


def _event_json(event: TaskStateEvent) -> str:
    return json.dumps(
        {
            "event_id": event.event_id,
            "task_id": event.task_id,
            "from_state": event.from_state,
            "event": event.event,
            "to_state": event.to_state,
            "timestamp": event.timestamp.isoformat(),
            "reason": event.reason,
        },
        sort_keys=True,
    )


def _event_from_json(payload: str) -> TaskStateEvent:
    raw = json.loads(payload)
    return TaskStateEvent(
        event_id=raw["event_id"],
        task_id=raw["task_id"],
        from_state=raw["from_state"],
        event=raw["event"],
        to_state=raw["to_state"],
        timestamp=datetime.fromisoformat(raw["timestamp"]),
        reason=raw["reason"],
    )


def _execution_json(execution: StepExecutionRecord) -> str:
    return json.dumps(
        {
            "step_id": execution.step_id,
            "tool_name": execution.tool_name,
            "started_at": execution.started_at.isoformat(),
            "completed_at": execution.completed_at.isoformat(),
            "duration_ms": execution.duration_ms,
            "attempt_count": execution.attempt_count,
            "executed": execution.executed,
            "input_summary": execution.input_summary.root,
            "output_summary": execution.output_summary.root,
            "failed_dependencies": list(execution.failed_dependencies),
            "attempts": [
                {
                    "attempt": item.attempt,
                    "tool_call_id": item.tool_call_id,
                    "status": item.status,
                    "duration_ms": item.duration_ms,
                    "error_code": item.error_code,
                }
                for item in execution.attempts
            ],
        },
        sort_keys=True,
    )


def _execution_from_json(payload: str) -> StepExecutionRecord:
    from copilot.services.workflows.models import ToolAttemptSummary

    raw = json.loads(payload)
    return StepExecutionRecord(
        step_id=raw["step_id"],
        tool_name=raw["tool_name"],
        started_at=datetime.fromisoformat(raw["started_at"]),
        completed_at=datetime.fromisoformat(raw["completed_at"]),
        duration_ms=raw["duration_ms"],
        attempt_count=raw["attempt_count"],
        executed=raw["executed"],
        input_summary=JsonObject(raw["input_summary"]),
        output_summary=JsonObject(raw["output_summary"]),
        failed_dependencies=tuple(raw["failed_dependencies"]),
        attempts=tuple(ToolAttemptSummary(**item) for item in raw["attempts"]),
    )
