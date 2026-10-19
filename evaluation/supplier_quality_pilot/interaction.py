"""Bounded scripted clarification through real PostgreSQL dispatch and Worker authority."""

from datetime import UTC, datetime
from uuid import uuid4

from copilot.bootstrap.container import WorkflowContainer
from copilot.contracts import ClarificationResponse, TaskStatus
from copilot.contracts.async_runtime import WorkerIdentity
from copilot.services.task_execution import TaskExecutionService
from copilot.services.task_intake import NaturalLanguageTaskCommand, TrustedCallerContext
from copilot.services.workflows.models import WorkflowExecution
from evaluation.supplier_quality_pilot.contracts import ClarificationObservation


def capture_execution(
    container: WorkflowContainer, task_id: str, tenant_id: str
) -> WorkflowExecution | None:
    """Read captured graph output only after comparing terminal result with the Task database."""
    state = container.engine.get_state(task_id, tenant_id)
    result = container.repository.task_result_for(task_id, tenant_id=tenant_id)
    if result is None:
        return None
    if result != state["task_result"]:
        raise ValueError("Task database and captured result disagree")
    plan = state.get("plan")
    results = {s.step_id: s for s in state["step_results"]}
    records = {s.step_id: s for s in state["step_executions"]}
    now = datetime.now(UTC)
    artifact = state.get("active_artifact")
    return WorkflowExecution(
        task_result=result,
        final_state=container.repository.state_for(task_id, tenant_id=tenant_id),
        step_results=tuple(results[s.step_id] for s in plan.steps if s.step_id in results)
        if plan
        else (),
        step_executions=tuple(records[s.step_id] for s in plan.steps if s.step_id in records)
        if plan
        else (),
        evidence=tuple(
            container.evidence.get(e, task_id=task_id, tenant_id=tenant_id)
            for e in state["evidence_ids"]
        ),
        artifacts=(artifact,) if artifact is not None else (),
        verification_result=state["verification_result"],
        started_at=state["started_at"],
        completed_at=now,
        duration_ms=max(0, round((now - state["started_at"]).total_seconds() * 1000)),
        trace_id=state["trace_id"],
        errors=tuple(state["errors"]),
    )


def scripted_clarification(
    container: WorkflowContainer,
    command: NaturalLanguageTaskCommand,
    caller: TrustedCallerContext,
    answer: str,
) -> tuple[WorkflowExecution | None, ClarificationObservation]:
    """Submit, observe suspension, persist the frozen answer, process the exact resume dispatch.

    This hosts the existing Worker application service synchronously, not a second lease or
    an engine.resume shortcut. PostgreSQL schema/checkpoint migrations must already exist.
    """
    runtime, queue, dispatcher, cancellations = (
        container.async_runtime_repository,
        container.task_queue,
        container.outbox_dispatcher,
        container.cancellations,
    )
    if (
        runtime is None
        or queue is None
        or dispatcher is None
        or cancellations is None
        or container.task_submission_service is None
    ):
        raise ValueError("P09 requires migrated PostgreSQL runtime and checkpoints")
    worker = TaskExecutionService(
        runtime=runtime,
        tasks=container.repository,
        approvals=container.approval_repository,
        clarifications=container.clarification_repository,
        queue=queue,
        engine=container.engine,
        worker=WorkerIdentity(
            worker_id=f"WORKER-PILOT-{uuid4().hex}",
            deployment_id="pilot",
            started_at=datetime.now(UTC),
        ),
        cancellations=cancellations,
        clock=lambda: datetime.now(UTC),
    )
    accepted = container.task_submission_service.submit(command, caller, idempotency_key=None)

    def process_one() -> str:
        dispatcher.dispatch_batch(limit=1)
        deliveries = queue.receive(max_messages=1, visibility_timeout_seconds=120)
        if len(deliveries) != 1 or deliveries[0].dispatch.task_id != accepted.task_id:
            raise ValueError("Dedicated pilot database has no matching dispatch")
        return worker.process(deliveries[0])

    first_disposition = process_one()
    initial = container.engine.get_state(accepted.task_id, caller.tenant_id)
    observation = ClarificationObservation(
        initial_task_id=accepted.task_id,
        initial_status=initial["domain_state"].state,
        tools_before_answer=sum(s.executed for s in initial["step_executions"]),
        answer=answer,
        first_disposition=first_disposition,
    )
    pending = container.clarification_repository.get_pending_for_task(
        accepted.task_id, tenant_id=caller.tenant_id
    )
    if initial["domain_state"].state is TaskStatus.WAITING_CLARIFICATION and pending is not None:
        submitted = container.clarification_service.respond(
            accepted.task_id,
            pending.clarification_id,
            ClarificationResponse(message=answer),
            caller,
        )
        observation.answer_applied = True
        observation.resume_dispatch_id = submitted.dispatch_id
        observation.resume_disposition = process_one()
    observation.final_task_id = accepted.task_id
    return capture_execution(container, accepted.task_id, caller.tenant_id), observation
