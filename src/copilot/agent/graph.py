"""LangGraph builder and stable workflow engine for Supplier Quality Analysis v1.1."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from functools import partial
from typing import cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from copilot.agent import nodes
from copilot.agent.routing import (
    route_after_classification,
    route_after_evidence,
    route_after_plan_creation,
    route_after_plan_repair,
    route_after_plan_validation,
    route_after_policy,
    route_after_replan,
    route_after_report,
    route_after_tool,
    route_after_understanding,
    route_after_validate,
    route_after_verification,
)
from copilot.agent.runtime import GraphNodeRuntime
from copilot.agent.state import AgentGraphState, initial_graph_state
from copilot.contracts import (
    ApprovalRequest,
    ApprovalResolutionAction,
    ApprovalStatus,
    TaskContract,
    TaskPlan,
    TaskRequest,
    TaskStatus,
)
from copilot.services.task_intake import RequestSource, TrustedTaskContext
from copilot.services.workflows.errors import WorkflowRecoveryError
from copilot.services.workflows.models import WorkflowExecution
from copilot.services.workflows.ports import EvidenceReader, IdentifierFactory, WorkflowRepository
from copilot.services.workflows.state_machine import TaskStateMachine


class WorkflowInterrupted(RuntimeError):
    """Raised to interfaces when a checkpointed task is waiting for external authority."""

    def __init__(
        self,
        message: str,
        *,
        task_id: str = "",
        trace_id: str = "",
        status: str = "",
        created_at: datetime | None = None,
        approval_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.task_id = task_id
        self.trace_id = trace_id
        self.status = status
        self.created_at = created_at
        self.approval_id = approval_id


def build_agent_graph(
    runtime: GraphNodeRuntime,
    *,
    checkpointer: BaseCheckpointSaver[str],
    interrupt_after: tuple[str, ...] = (),
) -> CompiledStateGraph[AgentGraphState, None, AgentGraphState, AgentGraphState]:
    """Compile the explicit graph with pure conditional routing and SQLite persistence."""
    builder = StateGraph(AgentGraphState)
    builder.add_node("validate_request", partial(nodes.validate_request, node_runtime=runtime))
    builder.add_node("understand_task", partial(nodes.understand_task, node_runtime=runtime))
    builder.add_node("classify_task", partial(nodes.classify_task, node_runtime=runtime))
    builder.add_node("create_plan", partial(nodes.create_plan, node_runtime=runtime))
    builder.add_node("validate_plan", partial(nodes.validate_plan, node_runtime=runtime))
    builder.add_node("repair_plan", partial(nodes.repair_plan, node_runtime=runtime))
    builder.add_node("replan", partial(nodes.replan, node_runtime=runtime))
    builder.add_node("policy_check", partial(nodes.policy_check, node_runtime=runtime))
    builder.add_node("execute_tool", partial(nodes.execute_tool, node_runtime=runtime))
    builder.add_node("aggregate_evidence", partial(nodes.aggregate_evidence, node_runtime=runtime))
    builder.add_node("verify_result", partial(nodes.verify_result, node_runtime=runtime))
    builder.add_node("generate_report", partial(nodes.generate_report, node_runtime=runtime))
    builder.add_node("persist_result", partial(nodes.persist_result, node_runtime=runtime))

    builder.add_edge(START, "validate_request")
    builder.add_conditional_edges("validate_request", route_after_validate)
    builder.add_conditional_edges("understand_task", route_after_understanding)
    builder.add_conditional_edges("classify_task", route_after_classification)
    builder.add_conditional_edges("create_plan", route_after_plan_creation)
    builder.add_conditional_edges("validate_plan", route_after_plan_validation)
    builder.add_conditional_edges("repair_plan", route_after_plan_repair)
    builder.add_conditional_edges("replan", route_after_replan)
    builder.add_conditional_edges("policy_check", route_after_policy)
    builder.add_conditional_edges("execute_tool", route_after_tool)
    builder.add_conditional_edges("generate_report", route_after_report)
    builder.add_conditional_edges("aggregate_evidence", route_after_evidence)
    builder.add_conditional_edges("verify_result", route_after_verification)
    builder.add_edge("persist_result", END)
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_after=list(interrupt_after) or None,
        name="supplier-quality-analysis-v1",
    )


class LangGraphWorkflowEngine:
    """Start, resume, and inspect one checkpointed workflow through a stable interface."""

    def __init__(
        self,
        *,
        runtime: GraphNodeRuntime,
        checkpointer: BaseCheckpointSaver[str],
        repository: WorkflowRepository,
        evidence_reader: EvidenceReader,
        state_machine: TaskStateMachine,
        ids: IdentifierFactory,
        clock: Callable[[], datetime],
        recursion_limit: int,
        max_task_steps: int,
        interrupt_after: tuple[str, ...] = (),
    ) -> None:
        self._runtime = runtime
        self._graph = build_agent_graph(
            runtime,
            checkpointer=checkpointer,
            interrupt_after=interrupt_after,
        )
        self._repository = repository
        self._evidence_reader = evidence_reader
        self._state_machine = state_machine
        self._ids = ids
        self._clock = clock
        self._recursion_limit = recursion_limit
        self._max_task_steps = max_task_steps

    def start(
        self,
        request: TaskRequest,
        contract: TaskContract,
        plan: TaskPlan,
    ) -> WorkflowExecution:
        """Initialize domain facts, acquire the task lease, and execute the graph."""
        started_at = self._clock()
        initial = self._state_machine.initial(contract.task_id)
        intake_context = TrustedTaskContext(
            task_id=contract.task_id,
            trace_id=contract.task_id,
            session_id=contract.task_id,
            user_id=request.user_id,
            tenant_id=contract.constraints.tenant_id,
            data_scope=contract.constraints.data_scope,
            authorized_supplier_ids=contract.constraints.supplier_ids,
            roles=("quality_analyst",),
            authentication_source="legacy_internal_adapter",
            is_demo_identity=True,
            purpose="supplier_quality_analysis.v1",
            output_format=contract.expected_output.artifact_type,
            max_steps=self._max_task_steps,
            read_only=True,
            require_approval=contract.approval_requirement.required,
            deadline_at=contract.constraints.deadline_at,
            request_source=RequestSource.INTERNAL,
            task_text_hash=hashlib.sha256(request.raw_input.encode("utf-8")).hexdigest(),
            task_text_length=len(request.raw_input),
        )
        self._repository.initialize(request, contract, plan, initial)
        owner_id = self._ids.new_id("LEASE")
        self._repository.acquire_execution(contract.task_id, owner_id)
        config = self._config(contract.task_id, contract.constraints.tenant_id)
        try:
            output = self._graph.invoke(
                initial_graph_state(
                    request=request,
                    intake_context=intake_context,
                    contract=contract,
                    plan=plan,
                    domain_state=initial,
                    started_at=started_at,
                ),
                config,
            )
            return self._execution(cast(AgentGraphState, output))
        finally:
            self._repository.release_execution(contract.task_id, owner_id)

    def submit(
        self,
        request: TaskRequest,
        intake_context: TrustedTaskContext,
    ) -> WorkflowExecution:
        """Persist a natural-language request before invoking task understanding."""
        started_at = self._clock()
        initial = self._state_machine.initial(intake_context.task_id)
        state = initial_graph_state(
            request=request,
            intake_context=intake_context,
            domain_state=initial,
            started_at=started_at,
        )
        self._repository.initialize(
            request,
            None,
            None,
            initial,
            task_id=intake_context.task_id,
        )
        self._runtime.record_submission(state)
        owner_id = self._ids.new_id("LEASE")
        self._repository.acquire_execution(intake_context.task_id, owner_id)
        config = self._config(intake_context.task_id, intake_context.tenant_id)
        try:
            output = self._graph.invoke(state, config)
            return self._execution(cast(AgentGraphState, output))
        finally:
            self._repository.release_execution(intake_context.task_id, owner_id)

    def resume(self, task_id: str, tenant_id: str) -> WorkflowExecution:
        """Continue from the latest safe checkpoint without replaying successful nodes."""
        config = self._config(task_id, tenant_id)
        snapshot = self._graph.get_state(config)
        if not snapshot.values:
            raise ValueError("workflow checkpoint was not found")
        current = cast(AgentGraphState, snapshot.values)
        if current["task_id"] != task_id or current["intake_context"].tenant_id != tenant_id:
            raise ValueError("workflow checkpoint scope does not match the requested task")
        if current["domain_state"] != self._repository.state_for(task_id):
            raise WorkflowRecoveryError(
                "workflow checkpoint does not match authoritative domain state"
            )
        if current["task_result"] is not None:
            raise ValueError("terminal task cannot be resumed")
        owner_id = self._ids.new_id("LEASE")
        self._repository.acquire_execution(task_id, owner_id)
        try:
            self._graph.update_state(
                config,
                {"resume_count": current["resume_count"] + 1},
            )
            output = self._graph.invoke(None, config)
            return self._execution(cast(AgentGraphState, output))
        finally:
            self._repository.release_execution(task_id, owner_id)

    def approval_state(self, task_id: str, tenant_id: str) -> dict[str, object]:
        """Return the minimized checkpoint facts needed to validate one decision."""
        state = self.get_state(task_id, tenant_id)
        step_id = state["approval_step_id"]
        return {
            "task_status": state["domain_state"].state.value,
            "tenant_id": state["intake_context"].tenant_id,
            "trace_id": state["trace_id"],
            "approval_id": state["approval_id"],
            "step_id": step_id,
            "planning_version": state["plan"].planning_version,
            "target_executed": any(call.step_id == step_id for call in state["tool_calls"]),
        }

    def resume_approval(
        self,
        approval: ApprovalRequest,
        tenant_id: str,
    ) -> WorkflowExecution | None:
        """Apply one durable approval decision and resume from the policy checkpoint."""
        config = self._config(approval.task_id, tenant_id)
        snapshot = self._graph.get_state(config)
        if not snapshot.values:
            raise ValueError("workflow checkpoint was not found")
        current = cast(AgentGraphState, snapshot.values)
        if (
            current["task_id"] != approval.task_id
            or current["intake_context"].tenant_id != tenant_id
            or current["approval_id"] != approval.approval_id
            or current["approval_step_id"] != approval.step_id
            or current["plan"].planning_version != approval.planning_version
        ):
            raise WorkflowRecoveryError("approval does not match the workflow checkpoint")
        if current["domain_state"] != self._repository.state_for(approval.task_id):
            raise WorkflowRecoveryError(
                "workflow checkpoint does not match authoritative domain state"
            )
        if current["domain_state"].state is not TaskStatus.WAITING_APPROVAL:
            raise WorkflowRecoveryError("task is not waiting for approval")
        if any(call.step_id == approval.step_id for call in current["tool_calls"]):
            raise WorkflowRecoveryError("approval target tool has already been called")
        event = _approval_event(approval)
        owner_id = self._ids.new_id("LEASE")
        self._repository.acquire_execution(approval.task_id, owner_id)
        try:
            domain_state, record = self._state_machine.transition(
                current["domain_state"],
                event,
                reason=f"Approval {approval.approval_id} resolved as {approval.status.value}",
            )
            self._repository.commit_transition(current["domain_state"], domain_state, record)
            approved = approval.status is ApprovalStatus.APPROVED
            self._graph.update_state(
                config,
                {
                    "domain_state": domain_state,
                    "route": "allowed" if approved else "approval_rejected",
                    "route_reason": (
                        "Approval resolved; execute the bound step"
                        if approved
                        else "Approval did not authorize execution"
                    ),
                    "last_arguments": approval.resolved_arguments if approved else None,
                    "resume_count": current["resume_count"] + 1,
                },
                as_node="policy_check",
            )
            output = self._graph.invoke(None, config)
            resumed = cast(AgentGraphState, output)
            return self._execution(resumed)
        finally:
            self._repository.release_execution(approval.task_id, owner_id)

    def get_state(self, task_id: str, tenant_id: str) -> AgentGraphState:
        """Return the latest checkpoint state after tenant/task validation."""
        snapshot = self._graph.get_state(self._config(task_id, tenant_id))
        if not snapshot.values:
            raise ValueError("workflow checkpoint was not found")
        state = cast(AgentGraphState, snapshot.values)
        if state["task_id"] != task_id or state["intake_context"].tenant_id != tenant_id:
            raise ValueError("workflow checkpoint scope does not match the requested task")
        return state

    def _config(self, task_id: str, tenant_id: str) -> RunnableConfig:
        return {
            "configurable": {"thread_id": f"{tenant_id}:{task_id}"},
            "recursion_limit": self._recursion_limit,
        }

    def _execution(self, state: AgentGraphState) -> WorkflowExecution:
        if state["task_result"] is None:
            raise WorkflowInterrupted(
                state["route_reason"],
                task_id=state["task_id"],
                trace_id=state["trace_id"],
                status=state["domain_state"].state.value,
                created_at=state["request"].created_at,
                approval_id=state["approval_id"],
            )
        completed_at = self._clock()
        results = {item.step_id: item for item in state["step_results"]}
        records = {item.step_id: item for item in state["step_executions"]}
        plan = state.get("plan")
        ordered_results = (
            tuple(results[step.step_id] for step in plan.steps if step.step_id in results)
            if plan is not None
            else ()
        )
        ordered_records = (
            tuple(records[step.step_id] for step in plan.steps if step.step_id in records)
            if plan is not None
            else ()
        )
        active_artifact = state.get("active_artifact")
        return WorkflowExecution(
            task_result=state["task_result"],
            final_state=state["domain_state"],
            step_results=ordered_results,
            step_executions=ordered_records,
            evidence=tuple(
                self._evidence_reader.get(evidence_id) for evidence_id in state["evidence_ids"]
            ),
            artifacts=(active_artifact,) if active_artifact is not None else (),
            verification_result=state["verification_result"],
            started_at=state["started_at"],
            completed_at=completed_at,
            duration_ms=max(0, round((completed_at - state["started_at"]).total_seconds() * 1000)),
            trace_id=state["trace_id"],
            errors=tuple(state["errors"]),
        )


def _approval_event(approval: ApprovalRequest) -> str:
    if approval.status is ApprovalStatus.APPROVED:
        return (
            "APPROVAL_EDITED"
            if approval.resolution_action is ApprovalResolutionAction.EDIT
            else "APPROVAL_GRANTED"
        )
    if approval.status is ApprovalStatus.REJECTED:
        return "APPROVAL_REJECTED"
    if approval.status is ApprovalStatus.EXPIRED:
        return "APPROVAL_EXPIRED"
    if approval.status is ApprovalStatus.REVOKED:
        return "APPROVAL_REVOKED"
    raise ValueError("pending approval cannot resume a workflow")


__all__ = [
    "LangGraphWorkflowEngine",
    "WorkflowInterrupted",
    "build_agent_graph",
]
