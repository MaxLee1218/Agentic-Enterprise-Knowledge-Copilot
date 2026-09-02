"""Workflow persistence with in-memory and SQLAlchemy-backed implementations."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import JSON, func, or_, select, update
from sqlalchemy import cast as sql_cast
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from copilot.contracts import (
    JsonObject,
    StepResult,
    TaskContract,
    TaskPlan,
    TaskRequest,
    TaskResult,
    TaskState,
    TaskStatus,
    ToolResult,
    VerificationResult,
)
from copilot.contracts.serialization import (
    deserialize_task_contract_json,
    deserialize_task_plan_json,
)
from copilot.persistence.database import PersistenceDatabase, coerce_database
from copilot.persistence.fencing import assert_fenced_session
from copilot.persistence.models import (
    TaskDispatchRow,
    WorkflowLeaseRow,
    WorkflowPlanHistoryRow,
    WorkflowStateEventRow,
    WorkflowStepResultRow,
    WorkflowTaskRow,
    WorkflowTaskRuntimeRow,
    WorkflowToolResultRow,
    WorkflowVerificationHistoryRow,
)
from copilot.services.workflows.models import StepExecutionRecord, TaskStateEvent


def _task_type_for_listing(request: TaskRequest, contract: TaskContract | None) -> str:
    if contract is not None:
        return contract.task_type.value
    intake = request.metadata.root.get("intake")
    if isinstance(intake, dict):
        value = intake.get("task_type")
        if isinstance(value, str) and value:
            return value
    return "supplier_quality_analysis.v1"


class WorkflowRepository:
    """Tenant-scoped workflow repository with in-memory and SQLAlchemy storage modes."""

    def __init__(
        self,
        database_path: PersistenceDatabase | Path | None = None,
        *,
        initialize_schema: bool = True,
    ) -> None:
        self._requests: dict[str, TaskRequest] = {}
        self._tenants: dict[str, str] = {}
        self._contracts: dict[str, TaskContract] = {}
        self._plans: dict[str, TaskPlan] = {}
        self._states: dict[str, TaskState] = {}
        self._state_events: list[TaskStateEvent] = []
        self._tool_results: list[ToolResult] = []
        self._step_results: dict[tuple[str, str, str], StepResult] = {}
        self._step_executions: dict[tuple[str, str, str], StepExecutionRecord] = {}
        self._task_results: dict[str, TaskResult] = {}
        self._verification_results: dict[str, VerificationResult] = {}
        self._execution_leases: dict[str, str] = {}
        self._lock = RLock()
        self._database, self._owns_database = coerce_database(
            database_path,
            initialize_schema=initialize_schema,
        )

    def initialize(
        self,
        request: TaskRequest,
        contract: TaskContract | None,
        plan: TaskPlan | None,
        state: TaskState,
        *,
        tenant_id: str,
        task_id: str | None = None,
    ) -> None:
        """Persist initial values exactly once per task."""
        resolved_task_id = task_id or (contract.task_id if contract is not None else state.task_id)
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if contract is not None and contract.constraints.tenant_id != tenant_id:
            raise ValueError("workflow contract tenant does not match persistence scope")
        with self._lock:
            if self._database is None:
                if resolved_task_id in self._states:
                    raise ValueError("workflow task already exists")
                self._requests[resolved_task_id] = request
                self._tenants[resolved_task_id] = tenant_id
                if contract is not None:
                    self._contracts[resolved_task_id] = contract
                if plan is not None:
                    self._plans[resolved_task_id] = plan
                self._states[resolved_task_id] = state
                return
            try:
                with self._database.session() as session:
                    session.add(
                        WorkflowTaskRow(
                            task_id=resolved_task_id,
                            tenant_id=tenant_id,
                            request_json=request.model_dump_json(),
                            contract_json=(contract.model_dump_json() if contract else None),
                            plan_json=plan.model_dump_json() if plan else None,
                            state_json=state.model_dump_json(),
                        )
                    )
            except IntegrityError as exc:
                raise ValueError("workflow task already exists") from exc

    def commit_transition(
        self,
        previous: TaskState,
        current: TaskState,
        event: TaskStateEvent,
        *,
        tenant_id: str,
    ) -> None:
        """Atomically compare state version, append event, and replace the snapshot."""
        if current.version != previous.version + 1:
            raise ValueError("task state compare-and-swap conflict")
        if event.event_id != current.last_event_id:
            raise ValueError("state event does not produce the supplied snapshot")
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(previous.task_id, tenant_id)
                authoritative = self._states.get(previous.task_id)
                if authoritative != previous:
                    raise ValueError("task state compare-and-swap conflict")
                self._states[current.task_id] = current
                self._state_events.append(event)
                return
            try:
                with self._database.session() as session:
                    assert_fenced_session(session, tenant_id=tenant_id, task_id=previous.task_id)
                    result = cast(
                        CursorResult[Any],
                        session.execute(
                            update(WorkflowTaskRow)
                            .where(
                                WorkflowTaskRow.task_id == previous.task_id,
                                WorkflowTaskRow.tenant_id == tenant_id,
                                WorkflowTaskRow.state_json == previous.model_dump_json(),
                            )
                            .values(state_json=current.model_dump_json())
                        ),
                    )
                    if result.rowcount != 1:
                        raise ValueError("task state compare-and-swap conflict")
                    session.add(
                        WorkflowStateEventRow(
                            event_id=event.event_id,
                            task_id=event.task_id,
                            tenant_id=tenant_id,
                            payload_json=_event_json(event),
                        )
                    )
            except IntegrityError as exc:
                raise ValueError("task state compare-and-swap conflict") from exc

    def save_contract(self, contract: TaskContract, *, tenant_id: str) -> None:
        """Persist the understanding result before any business tool execution."""
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(contract.task_id, tenant_id)
                if contract.task_id not in self._states:
                    raise ValueError("workflow task was not initialized")
                if any(result.task_id == contract.task_id for result in self._tool_results):
                    raise ValueError("contract cannot change after tool execution")
                existing = self._contracts.get(contract.task_id)
                if existing is not None and contract.contract_version < existing.contract_version:
                    raise ValueError("contract version cannot decrease")
                self._contracts[contract.task_id] = contract
                return
            with self._database.session() as session:
                assert_fenced_session(session, tenant_id=tenant_id, task_id=contract.task_id)
                row = session.scalar(
                    select(WorkflowTaskRow).where(
                        WorkflowTaskRow.task_id == contract.task_id,
                        WorkflowTaskRow.tenant_id == tenant_id,
                    )
                )
                if row is None:
                    raise ValueError("workflow task was not initialized")
                attempt = session.scalar(
                    select(WorkflowToolResultRow.sequence_id).where(
                        WorkflowToolResultRow.task_id == contract.task_id,
                        WorkflowToolResultRow.tenant_id == tenant_id,
                    )
                )
                if attempt is not None:
                    raise ValueError("contract cannot change after tool execution")
                if row.contract_json is not None:
                    existing = deserialize_task_contract_json(row.contract_json)
                    if contract.contract_version < existing.contract_version:
                        raise ValueError("contract version cannot decrease")
                row.contract_json = contract.model_dump_json()

    def save_plan(self, plan: TaskPlan, *, tenant_id: str) -> None:
        """Persist the current plan while retaining immutable prior versions."""
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(plan.task_id, tenant_id)
                if plan.task_id not in self._states:
                    raise ValueError("workflow task was not initialized")
                existing = self._plans.get(plan.task_id)
                if existing == plan:
                    return
                executed = any(result.task_id == plan.task_id for result in self._tool_results)
                if (
                    executed
                    and existing is not None
                    and plan.planning_version <= existing.planning_version
                ):
                    raise ValueError("replan version must increase after tool execution")
                if (
                    not executed
                    and existing is not None
                    and plan.planning_version < existing.planning_version
                ):
                    raise ValueError("plan version cannot decrease")
                self._plans[plan.task_id] = plan
                return
            with self._database.session() as session:
                assert_fenced_session(session, tenant_id=tenant_id, task_id=plan.task_id)
                row = session.scalar(
                    select(WorkflowTaskRow).where(
                        WorkflowTaskRow.task_id == plan.task_id,
                        WorkflowTaskRow.tenant_id == tenant_id,
                    )
                )
                if row is None:
                    raise ValueError("workflow task was not initialized")
                existing = deserialize_task_plan_json(row.plan_json) if row.plan_json else None
                if existing == plan:
                    return
                executed = session.scalar(
                    select(WorkflowToolResultRow.sequence_id).where(
                        WorkflowToolResultRow.task_id == plan.task_id,
                        WorkflowToolResultRow.tenant_id == tenant_id,
                    )
                )
                if (
                    executed is not None
                    and existing is not None
                    and plan.planning_version <= existing.planning_version
                ):
                    raise ValueError("replan version must increase after tool execution")
                if (
                    executed is None
                    and existing is not None
                    and plan.planning_version < existing.planning_version
                ):
                    raise ValueError("plan version cannot decrease")
                if existing is not None:
                    prior = session.scalar(
                        select(WorkflowPlanHistoryRow.sequence_id).where(
                            WorkflowPlanHistoryRow.task_id == existing.task_id,
                            WorkflowPlanHistoryRow.tenant_id == tenant_id,
                            WorkflowPlanHistoryRow.planning_version == existing.planning_version,
                            WorkflowPlanHistoryRow.plan_json == existing.model_dump_json(),
                        )
                    )
                    if prior is None:
                        session.add(
                            WorkflowPlanHistoryRow(
                                task_id=existing.task_id,
                                tenant_id=tenant_id,
                                planning_version=existing.planning_version,
                                plan_json=existing.model_dump_json(),
                            )
                        )
                row.plan_json = plan.model_dump_json()

    def save_tool_result(self, result: ToolResult, *, tenant_id: str) -> None:
        """Append one unique immutable tool attempt."""
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(result.task_id, tenant_id)
                existing = next(
                    (
                        item
                        for item in self._tool_results
                        if item.tool_call_id == result.tool_call_id
                    ),
                    None,
                )
                if existing == result:
                    return
                if existing is not None:
                    raise ValueError("tool result already exists")
                self._tool_results.append(result)
                return
            with self._database.session() as session:
                assert_fenced_session(session, tenant_id=tenant_id, task_id=result.task_id)
                payload = session.scalar(
                    select(WorkflowToolResultRow.payload_json).where(
                        WorkflowToolResultRow.tool_call_id == result.tool_call_id,
                        WorkflowToolResultRow.task_id == result.task_id,
                        WorkflowToolResultRow.tenant_id == tenant_id,
                    )
                )
                if payload is not None:
                    if ToolResult.model_validate_json(payload) == result:
                        return
                    raise ValueError("tool result already exists")
                session.add(
                    WorkflowToolResultRow(
                        tool_call_id=result.tool_call_id,
                        task_id=result.task_id,
                        tenant_id=tenant_id,
                        payload_json=result.model_dump_json(),
                    )
                )

    def save_step_result(
        self,
        task_id: str,
        result: StepResult,
        execution: StepExecutionRecord,
        *,
        tenant_id: str,
    ) -> None:
        """Save exactly one final result per planned step."""
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(task_id, tenant_id)
                key = (tenant_id, task_id, result.step_id)
                existing = self._step_results.get(key)
                if existing is not None:
                    if existing == result and self._step_executions[key] == execution:
                        return
                    raise ValueError("step result already exists")
                self._step_results[key] = result
                self._step_executions[key] = execution
                return
            with self._database.session() as session:
                assert_fenced_session(session, tenant_id=tenant_id, task_id=task_id)
                row = session.scalar(
                    select(WorkflowStepResultRow).where(
                        WorkflowStepResultRow.step_id == result.step_id,
                        WorkflowStepResultRow.task_id == task_id,
                        WorkflowStepResultRow.tenant_id == tenant_id,
                    )
                )
                if row is not None:
                    if (
                        StepResult.model_validate_json(row.result_json) == result
                        and _execution_from_json(row.execution_json) == execution
                    ):
                        return
                    raise ValueError("step result already exists")
                self._validate_step_for_task(session, tenant_id, task_id, result.step_id)
                session.add(
                    WorkflowStepResultRow(
                        step_id=result.step_id,
                        task_id=task_id,
                        tenant_id=tenant_id,
                        result_json=result.model_dump_json(),
                        execution_json=_execution_json(execution),
                    )
                )

    def save_task_result(self, result: TaskResult, *, tenant_id: str) -> None:
        """Save exactly one terminal result per task."""
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(result.task_id, tenant_id)
                existing = self._task_results.get(result.task_id)
                if existing == result:
                    return
                if existing is not None:
                    raise ValueError("task result already exists")
                self._task_results[result.task_id] = result
                return
            with self._database.session() as session:
                assert_fenced_session(session, tenant_id=tenant_id, task_id=result.task_id)
                row = session.scalar(
                    select(WorkflowTaskRow).where(
                        WorkflowTaskRow.task_id == result.task_id,
                        WorkflowTaskRow.tenant_id == tenant_id,
                    )
                )
                if row is None:
                    raise ValueError("workflow task was not initialized")
                if row.task_result_json is not None:
                    if TaskResult.model_validate_json(row.task_result_json) == result:
                        return
                    raise ValueError("task result already exists")
                row.task_result_json = result.model_dump_json()

    def save_verification_result(self, result: VerificationResult, *, tenant_id: str) -> None:
        """Append verification history and retain the latest result for recovery."""
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(result.task_id, tenant_id)
                if self._verification_results.get(result.task_id) == result:
                    return
                self._verification_results[result.task_id] = result
                return
            with self._database.session() as session:
                assert_fenced_session(session, tenant_id=tenant_id, task_id=result.task_id)
                row = session.scalar(
                    select(WorkflowTaskRow).where(
                        WorkflowTaskRow.task_id == result.task_id,
                        WorkflowTaskRow.tenant_id == tenant_id,
                    )
                )
                if row is None:
                    raise ValueError("workflow task was not initialized")
                if row.verification_json == result.model_dump_json():
                    return
                if row.verification_json is not None:
                    prior = session.scalar(
                        select(WorkflowVerificationHistoryRow.sequence_id).where(
                            WorkflowVerificationHistoryRow.task_id == result.task_id,
                            WorkflowVerificationHistoryRow.tenant_id == tenant_id,
                            WorkflowVerificationHistoryRow.verification_json
                            == row.verification_json,
                        )
                    )
                    if prior is None:
                        session.add(
                            WorkflowVerificationHistoryRow(
                                task_id=result.task_id,
                                tenant_id=tenant_id,
                                verification_json=row.verification_json,
                            )
                        )
                row.verification_json = result.model_dump_json()

    def acquire_execution(self, task_id: str, owner_id: str, *, tenant_id: str) -> None:
        """Compatibility claim for the pre-Stage-E synchronous execution host.

        The future Worker uses ``AsyncRuntimeRepository.try_acquire_lease``. This path still writes
        the evolved single lease table so deployment of the Stage B migration does not introduce a
        second lock or break the current synchronous API before the Queue/Worker gates pass.
        """
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(task_id, tenant_id)
                owner = self._execution_leases.get(task_id)
                if owner is not None and owner != owner_id:
                    raise ValueError("task execution lease conflict")
                if task_id in self._task_results:
                    raise ValueError("terminal task cannot be resumed")
                self._execution_leases[task_id] = owner_id
                return
            try:
                with self._database.session() as session:
                    task = session.scalar(
                        select(WorkflowTaskRow)
                        .where(
                            WorkflowTaskRow.task_id == task_id,
                            WorkflowTaskRow.tenant_id == tenant_id,
                        )
                        .with_for_update()
                    )
                    if task is None:
                        raise ValueError("workflow task was not initialized")
                    if task.task_result_json is not None:
                        raise ValueError("terminal task cannot be resumed")
                    observed = session.scalar(select(func.now()))
                    if not isinstance(observed, datetime):
                        raise RuntimeError("database did not return a timestamp")
                    now = _aware_utc(observed)
                    state = TaskState.model_validate_json(task.state_json)
                    runtime = session.scalar(
                        select(WorkflowTaskRuntimeRow)
                        .where(
                            WorkflowTaskRuntimeRow.task_id == task_id,
                            WorkflowTaskRuntimeRow.tenant_id == tenant_id,
                        )
                        .with_for_update()
                    )
                    if runtime is None:
                        runtime = WorkflowTaskRuntimeRow(
                            task_id=task_id,
                            tenant_id=tenant_id,
                            runtime_status="READY",
                            execution_generation=1,
                            fencing_counter=0,
                            recovery_attempt_count=0,
                            created_at=now,
                            updated_at=now,
                        )
                        session.add(runtime)
                        session.flush()
                    existing_lease = session.scalar(
                        select(WorkflowLeaseRow)
                        .where(
                            WorkflowLeaseRow.task_id == task_id,
                            WorkflowLeaseRow.tenant_id == tenant_id,
                        )
                        .with_for_update()
                    )
                    if existing_lease is not None:
                        if (
                            existing_lease.worker_id == owner_id
                            and _aware_utc(existing_lease.expires_at) > now
                        ):
                            return
                        if _aware_utc(existing_lease.expires_at) > now:
                            raise ValueError("task execution lease conflict")
                        session.delete(existing_lease)
                        session.flush()
                    dispatch = (
                        session.scalar(
                            select(TaskDispatchRow).where(
                                TaskDispatchRow.tenant_id == tenant_id,
                                TaskDispatchRow.dispatch_id == runtime.current_dispatch_id,
                            )
                        )
                        if runtime.current_dispatch_id is not None
                        else None
                    )
                    if dispatch is None or dispatch.status != "ENQUEUED":
                        if dispatch is not None:
                            runtime.execution_generation += 1
                        dispatch_id = f"D-INLINE-{uuid4().hex}"
                        dispatch = TaskDispatchRow(
                            tenant_id=tenant_id,
                            dispatch_id=dispatch_id,
                            task_id=task_id,
                            execution_generation=runtime.execution_generation,
                            expected_task_version=state.version,
                            trace_id=_request_id_for(task.request_json, task_id),
                            status="ENQUEUED",
                            available_at=now,
                            attempt_count=1,
                            created_at=now,
                            updated_at=now,
                        )
                        session.add(dispatch)
                        session.flush()
                        runtime.current_dispatch_id = dispatch_id
                    runtime.fencing_counter += 1
                    runtime.runtime_status = "LEASED"
                    runtime.updated_at = now
                    session.add(
                        WorkflowLeaseRow(
                            task_id=task_id,
                            tenant_id=tenant_id,
                            dispatch_id=dispatch.dispatch_id,
                            execution_generation=runtime.execution_generation,
                            task_version=state.version,
                            worker_id=owner_id,
                            lease_id=f"L-INLINE-{uuid4().hex}",
                            fencing_token=runtime.fencing_counter,
                            acquired_at=now,
                            heartbeat_at=now,
                            expires_at=now + timedelta(minutes=10),
                        )
                    )
            except IntegrityError as exc:
                raise ValueError("task execution lease conflict") from exc

    def release_execution(self, task_id: str, owner_id: str, *, tenant_id: str) -> None:
        """Release only the caller's task lease."""
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(task_id, tenant_id)
                if self._execution_leases.get(task_id) == owner_id:
                    del self._execution_leases[task_id]
                return
            with self._database.session() as session:
                lease = session.scalar(
                    select(WorkflowLeaseRow).where(
                        WorkflowLeaseRow.task_id == task_id,
                        WorkflowLeaseRow.tenant_id == tenant_id,
                        WorkflowLeaseRow.worker_id == owner_id,
                    )
                )
                if lease is None:
                    return
                dispatch_id = lease.dispatch_id
                session.delete(lease)
                task = session.scalar(
                    select(WorkflowTaskRow).where(
                        WorkflowTaskRow.task_id == task_id,
                        WorkflowTaskRow.tenant_id == tenant_id,
                    )
                )
                runtime = session.scalar(
                    select(WorkflowTaskRuntimeRow).where(
                        WorkflowTaskRuntimeRow.task_id == task_id,
                        WorkflowTaskRuntimeRow.tenant_id == tenant_id,
                    )
                )
                observed = session.scalar(select(func.now()))
                if not isinstance(observed, datetime):
                    raise RuntimeError("database did not return a timestamp")
                now = _aware_utc(observed)
                if task is not None and runtime is not None:
                    state = TaskState.model_validate_json(task.state_json)
                    if state.state in {
                        TaskStatus.COMPLETED,
                        TaskStatus.FAILED,
                        TaskStatus.CANCELLED,
                    }:
                        runtime.runtime_status = "FINISHED"
                    elif state.state in {
                        TaskStatus.WAITING_APPROVAL,
                        TaskStatus.WAITING_CLARIFICATION,
                    }:
                        runtime.runtime_status = "SUSPENDED"
                    else:
                        runtime.runtime_status = "READY"
                    runtime.updated_at = now
                dispatch = session.scalar(
                    select(TaskDispatchRow).where(
                        TaskDispatchRow.tenant_id == tenant_id,
                        TaskDispatchRow.dispatch_id == dispatch_id,
                    )
                )
                if dispatch is not None and dispatch.status == "ENQUEUED":
                    dispatch.status = "ACKNOWLEDGED"
                    dispatch.updated_at = now

    def state_for(self, task_id: str, *, tenant_id: str) -> TaskState:
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(task_id, tenant_id)
                return self._states[task_id]
            with self._database.session() as session:
                row = self._task_row(session, task_id, tenant_id)
                if row is None:
                    raise KeyError(task_id)
                return TaskState.model_validate_json(row.state_json)

    def list_task_ids(
        self,
        *,
        tenant_id: str,
        user_id: str,
        assigned_task_ids: tuple[str, ...] = (),
        allowed_task_types: tuple[str, ...] = (),
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[tuple[str, ...], int]:
        """List tenant-scoped owned or explicitly assigned tasks with bounded pagination."""
        if not tenant_id or not user_id:
            raise ValueError("tenant_id and user_id are required")
        if limit < 1 or limit > 100 or offset < 0:
            raise ValueError("task list pagination is outside the supported bounds")
        with self._lock:
            if self._database is None:
                matching = [
                    (task_id, request.created_at)
                    for task_id, request in self._requests.items()
                    if self._tenants.get(task_id) == tenant_id
                    and (request.user_id == user_id or task_id in assigned_task_ids)
                    and (
                        not allowed_task_types
                        or _task_type_for_listing(
                            request,
                            self._contracts.get(task_id),
                        )
                        in allowed_task_types
                    )
                    and (status is None or self._states[task_id].state.value == status)
                ]
                matching.sort(key=lambda item: (item[1], item[0]), reverse=True)
                return (
                    tuple(task_id for task_id, _created_at in matching[offset : offset + limit]),
                    len(matching),
                )

            if self._database.backend == "sqlite":
                request_user_id = func.json_extract(WorkflowTaskRow.request_json, "$.user_id")
                task_status = func.json_extract(WorkflowTaskRow.state_json, "$.state")
                created_at = func.json_extract(WorkflowTaskRow.request_json, "$.created_at")
                contract_task_type = func.json_extract(WorkflowTaskRow.contract_json, "$.task_type")
                intake_task_type = func.json_extract(
                    WorkflowTaskRow.request_json, "$.metadata.intake.task_type"
                )
            else:
                request_json = sql_cast(WorkflowTaskRow.request_json, JSON)
                state_json = sql_cast(WorkflowTaskRow.state_json, JSON)
                contract_json = sql_cast(WorkflowTaskRow.contract_json, JSON)
                request_user_id = request_json["user_id"].as_string()
                task_status = state_json["state"].as_string()
                created_at = request_json["created_at"].as_string()
                contract_task_type = contract_json["task_type"].as_string()
                intake_task_type = request_json["metadata"]["intake"]["task_type"].as_string()
            access_condition = request_user_id == user_id
            if assigned_task_ids:
                access_condition = or_(
                    access_condition,
                    WorkflowTaskRow.task_id.in_(assigned_task_ids),
                )
            conditions = [
                WorkflowTaskRow.tenant_id == tenant_id,
                access_condition,
            ]
            if allowed_task_types:
                persisted_task_type = func.coalesce(
                    contract_task_type,
                    intake_task_type,
                    "supplier_quality_analysis.v1",
                )
                conditions.append(persisted_task_type.in_(allowed_task_types))
            if status is not None:
                conditions.append(task_status == status)
            with self._database.session() as session:
                total = session.scalar(
                    select(func.count()).select_from(WorkflowTaskRow).where(*conditions)
                )
                task_ids = session.scalars(
                    select(WorkflowTaskRow.task_id)
                    .where(*conditions)
                    .order_by(
                        created_at.desc(),
                        WorkflowTaskRow.task_id.desc(),
                    )
                    .offset(offset)
                    .limit(limit)
                )
                return tuple(task_ids), int(total or 0)

    def request_for(self, task_id: str, *, tenant_id: str) -> TaskRequest:
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(task_id, tenant_id)
                return self._requests[task_id]
            with self._database.session() as session:
                row = self._task_row(session, task_id, tenant_id)
                if row is None:
                    raise KeyError(task_id)
                return TaskRequest.model_validate_json(row.request_json)

    def contract_for(self, task_id: str, *, tenant_id: str) -> TaskContract | None:
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(task_id, tenant_id)
                return self._contracts.get(task_id)
            with self._database.session() as session:
                row = self._task_row(session, task_id, tenant_id)
                if row is None:
                    raise KeyError(task_id)
                return (
                    deserialize_task_contract_json(row.contract_json) if row.contract_json else None
                )

    def plan_for(self, task_id: str, *, tenant_id: str) -> TaskPlan | None:
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(task_id, tenant_id)
                return self._plans.get(task_id)
            with self._database.session() as session:
                row = self._task_row(session, task_id, tenant_id)
                if row is None:
                    raise KeyError(task_id)
                return deserialize_task_plan_json(row.plan_json) if row.plan_json else None

    def task_result_for(self, task_id: str, *, tenant_id: str) -> TaskResult | None:
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(task_id, tenant_id)
                return self._task_results.get(task_id)
            with self._database.session() as session:
                row = self._task_row(session, task_id, tenant_id)
                if row is None:
                    raise KeyError(task_id)
                return (
                    TaskResult.model_validate_json(row.task_result_json)
                    if row.task_result_json
                    else None
                )

    def step_results_for(self, task_id: str, *, tenant_id: str) -> tuple[StepResult, ...]:
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(task_id, tenant_id)
                plan = self._plans.get(task_id)
                if plan is None:
                    return ()
                return tuple(
                    self._step_results[(tenant_id, task_id, step.step_id)]
                    for step in plan.steps
                    if (tenant_id, task_id, step.step_id) in self._step_results
                )
            with self._database.session() as session:
                payloads = session.scalars(
                    select(WorkflowStepResultRow.result_json)
                    .where(
                        WorkflowStepResultRow.task_id == task_id,
                        WorkflowStepResultRow.tenant_id == tenant_id,
                    )
                    .order_by(WorkflowStepResultRow.sequence_id)
                )
                return tuple(StepResult.model_validate_json(payload) for payload in payloads)

    def step_execution_for(
        self, task_id: str, step_id: str, *, tenant_id: str
    ) -> StepExecutionRecord | None:
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(task_id, tenant_id)
                return self._step_executions.get((tenant_id, task_id, step_id))
            with self._database.session() as session:
                payload = session.scalar(
                    select(WorkflowStepResultRow.execution_json).where(
                        WorkflowStepResultRow.step_id == step_id,
                        WorkflowStepResultRow.task_id == task_id,
                        WorkflowStepResultRow.tenant_id == tenant_id,
                    )
                )
                return _execution_from_json(payload) if payload else None

    def tool_results_for(self, task_id: str, *, tenant_id: str) -> tuple[ToolResult, ...]:
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(task_id, tenant_id)
                return tuple(result for result in self._tool_results if result.task_id == task_id)
            with self._database.session() as session:
                payloads = session.scalars(
                    select(WorkflowToolResultRow.payload_json)
                    .where(
                        WorkflowToolResultRow.task_id == task_id,
                        WorkflowToolResultRow.tenant_id == tenant_id,
                    )
                    .order_by(WorkflowToolResultRow.sequence_id)
                )
                return tuple(ToolResult.model_validate_json(payload) for payload in payloads)

    def state_events_for(self, task_id: str, *, tenant_id: str) -> tuple[TaskStateEvent, ...]:
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(task_id, tenant_id)
                return tuple(event for event in self._state_events if event.task_id == task_id)
            with self._database.session() as session:
                payloads = session.scalars(
                    select(WorkflowStateEventRow.payload_json)
                    .where(
                        WorkflowStateEventRow.task_id == task_id,
                        WorkflowStateEventRow.tenant_id == tenant_id,
                    )
                    .order_by(WorkflowStateEventRow.sequence_id)
                )
                return tuple(_event_from_json(payload) for payload in payloads)

    def verification_result_for(self, task_id: str, *, tenant_id: str) -> VerificationResult:
        with self._lock:
            if self._database is None:
                self._require_in_memory_tenant(task_id, tenant_id)
                return self._verification_results[task_id]
            with self._database.session() as session:
                row = self._task_row(session, task_id, tenant_id)
                if row is None or row.verification_json is None:
                    raise KeyError(task_id)
                return VerificationResult.model_validate_json(row.verification_json)

    def close(self) -> None:
        if self._owns_database and self._database is not None:
            self._database.dispose()
            self._database = None

    def _validate_step_for_task(
        self, session: object, tenant_id: str, task_id: str, step_id: str
    ) -> None:
        from sqlalchemy.orm import Session

        assert isinstance(session, Session)
        row = self._task_row(session, task_id, tenant_id)
        if row is not None and row.plan_json is not None:
            plan = deserialize_task_plan_json(row.plan_json)
            if any(step.step_id == step_id for step in plan.steps):
                return
        raise ValueError("step does not belong to a persisted plan")

    def _require_in_memory_tenant(self, task_id: str, tenant_id: str) -> None:
        if self._tenants.get(task_id) != tenant_id:
            raise KeyError(task_id)

    @staticmethod
    def _task_row(session: object, task_id: str, tenant_id: str) -> WorkflowTaskRow | None:
        from sqlalchemy.orm import Session

        assert isinstance(session, Session)
        return session.scalar(
            select(WorkflowTaskRow).where(
                WorkflowTaskRow.task_id == task_id,
                WorkflowTaskRow.tenant_id == tenant_id,
            )
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


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _request_id_for(request_json: str, task_id: str) -> str:
    """Return a safe compatibility trace identity from the persisted Task request."""
    try:
        payload = json.loads(request_json)
    except json.JSONDecodeError:
        return f"TRACE-{task_id}"[:200]
    request_id = payload.get("id") if isinstance(payload, dict) else None
    return str(request_id or f"TRACE-{task_id}")[:200]


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


# Compatibility alias for older imports.  The implementation is no longer memory-specific.
InMemoryWorkflowRepository = WorkflowRepository

__all__ = ["InMemoryWorkflowRepository", "WorkflowRepository"]
