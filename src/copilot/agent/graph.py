"""LangGraph builder and stable workflow engine for Supplier Quality Analysis v1.0."""

from __future__ import annotations

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
from copilot.contracts import TaskContract, TaskPlan, TaskRequest
from copilot.services.workflows.errors import WorkflowRecoveryError
from copilot.services.workflows.models import WorkflowExecution
from copilot.services.workflows.ports import EvidenceReader, IdentifierFactory, WorkflowRepository
from copilot.services.workflows.state_machine import TaskStateMachine


class WorkflowInterrupted(RuntimeError):
    """Raised to interfaces when a checkpointed task is waiting for external authority."""


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
        interrupt_after: tuple[str, ...] = (),
    ) -> None:
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

    def start(
        self,
        request: TaskRequest,
        contract: TaskContract,
        plan: TaskPlan,
    ) -> WorkflowExecution:
        """Initialize domain facts, acquire the task lease, and execute the graph."""
        started_at = self._clock()
        initial = self._state_machine.initial(contract.task_id)
        self._repository.initialize(request, contract, plan, initial)
        owner_id = self._ids.new_id("LEASE")
        self._repository.acquire_execution(contract.task_id, owner_id)
        config = self._config(contract.task_id, contract.constraints.tenant_id)
        try:
            output = self._graph.invoke(
                initial_graph_state(
                    request=request,
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

    def resume(self, task_id: str, tenant_id: str) -> WorkflowExecution:
        """Continue from the latest safe checkpoint without replaying successful nodes."""
        config = self._config(task_id, tenant_id)
        snapshot = self._graph.get_state(config)
        if not snapshot.values:
            raise ValueError("workflow checkpoint was not found")
        current = cast(AgentGraphState, snapshot.values)
        if current["task_id"] != task_id or current["contract"].constraints.tenant_id != tenant_id:
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

    def get_state(self, task_id: str, tenant_id: str) -> AgentGraphState:
        """Return the latest checkpoint state after tenant/task validation."""
        snapshot = self._graph.get_state(self._config(task_id, tenant_id))
        if not snapshot.values:
            raise ValueError("workflow checkpoint was not found")
        state = cast(AgentGraphState, snapshot.values)
        if state["task_id"] != task_id or state["contract"].constraints.tenant_id != tenant_id:
            raise ValueError("workflow checkpoint scope does not match the requested task")
        return state

    def _config(self, task_id: str, tenant_id: str) -> RunnableConfig:
        return {
            "configurable": {"thread_id": f"{tenant_id}:{task_id}"},
            "recursion_limit": self._recursion_limit,
        }

    def _execution(self, state: AgentGraphState) -> WorkflowExecution:
        if state["task_result"] is None:
            raise WorkflowInterrupted(state["route_reason"])
        completed_at = self._clock()
        results = {item.step_id: item for item in state["step_results"]}
        records = {item.step_id: item for item in state["step_executions"]}
        ordered_results = tuple(
            results[step.step_id] for step in state["plan"].steps if step.step_id in results
        )
        ordered_records = tuple(
            records[step.step_id] for step in state["plan"].steps if step.step_id in records
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
        )


__all__ = [
    "LangGraphWorkflowEngine",
    "WorkflowInterrupted",
    "build_agent_graph",
]
