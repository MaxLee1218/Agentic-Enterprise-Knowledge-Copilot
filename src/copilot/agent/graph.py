"""LangGraph builder and stable shared workflow engine for governed domain tasks."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import datetime
from functools import partial
from time import monotonic
from typing import cast

from langchain_core.runnables import RunnableConfig, RunnableLambda
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
    AccountsPayableConstraintsV1,
    ApprovalRequest,
    ApprovalResolutionAction,
    ApprovalStatus,
    ClarificationStatus,
    SpanKind,
    SpanStatus,
    TaskClarification,
    TaskContract,
    TaskPlan,
    TaskRequest,
    TaskStatus,
    TaskType,
)
from copilot.contracts.async_runtime import CheckpointIdentity
from copilot.services.observability import (
    EventName,
    NoopObservability,
    ObservabilityPort,
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
        clarification_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.task_id = task_id
        self.trace_id = trace_id
        self.status = status
        self.created_at = created_at
        self.approval_id = approval_id
        self.clarification_id = clarification_id


def build_agent_graph(
    runtime: GraphNodeRuntime,
    *,
    checkpointer: BaseCheckpointSaver[str],
    interrupt_after: tuple[str, ...] = (),
    observability: ObservabilityPort | None = None,
) -> CompiledStateGraph[AgentGraphState, None, AgentGraphState, AgentGraphState]:
    """Compile the explicit graph with pure conditional routing and SQLite persistence."""
    telemetry = observability or NoopObservability()
    builder = StateGraph(AgentGraphState)
    node_functions = {
        "validate_request": nodes.validate_request,
        "understand_task": nodes.understand_task,
        "request_clarification": nodes.request_clarification,
        "classify_task": nodes.classify_task,
        "create_plan": nodes.create_plan,
        "validate_plan": nodes.validate_plan,
        "repair_plan": nodes.repair_plan,
        "replan": nodes.replan,
        "policy_check": nodes.policy_check,
        "execute_tool": nodes.execute_tool,
        "aggregate_evidence": nodes.aggregate_evidence,
        "verify_result": nodes.verify_result,
        "generate_report": nodes.generate_report,
        "persist_result": nodes.persist_result,
    }
    for node_name, function in node_functions.items():
        bound = partial(function, node_runtime=runtime)
        node_action = cast(Callable[[AgentGraphState], dict[str, object]], bound)
        builder.add_node(
            node_name,
            RunnableLambda(telemetry.instrument_node(node_name, node_action)),
        )

    builder.add_edge(START, "validate_request")
    builder.add_conditional_edges("validate_request", route_after_validate)
    builder.add_conditional_edges("understand_task", route_after_understanding)
    builder.add_edge("request_clarification", "understand_task")
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
        interrupt_after=list(dict.fromkeys(("request_clarification", *interrupt_after))),
        name="governed-enterprise-analysis",
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
        observability: ObservabilityPort | None = None,
        timer: Callable[[], float] = monotonic,
    ) -> None:
        self._runtime = runtime
        self._observability = observability or NoopObservability()
        self._timer = timer
        self._graph = build_agent_graph(
            runtime,
            checkpointer=checkpointer,
            interrupt_after=interrupt_after,
            observability=self._observability,
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
        ap_scope = (
            contract.constraints
            if isinstance(contract.constraints, AccountsPayableConstraintsV1)
            else None
        )
        intake_context = TrustedTaskContext(
            task_id=contract.task_id,
            trace_id=contract.task_id,
            session_id=contract.task_id,
            user_id=request.user_id,
            tenant_id=contract.constraints.tenant_id,
            data_scope=contract.constraints.data_scope,
            authorized_supplier_ids=contract.constraints.supplier_ids,
            authorized_legal_entity_ids=(ap_scope.legal_entity_ids if ap_scope else ()),
            authorized_business_unit_ids=(ap_scope.business_unit_ids if ap_scope else ()),
            authorized_currency_scope=(ap_scope.currency_scope if ap_scope else ()),
            policy_rule_set_id=(ap_scope.policy_rule_set_id if ap_scope else None),
            policy_rule_set_version=(ap_scope.policy_rule_set_version if ap_scope else None),
            policy_manifest_checksum=(ap_scope.policy_manifest_checksum if ap_scope else None),
            policy_materiality=(ap_scope.effective_materiality if ap_scope else ()),
            policy_snapshot_at=(ap_scope.snapshot_at if ap_scope else None),
            roles=(
                ("quality_analyst",)
                if contract.task_type is TaskType.SUPPLIER_QUALITY_ANALYSIS_V1
                else ("finance_analyst",)
            ),
            scopes=(
                ("task:execute", "data:quality.v1")
                if contract.task_type is TaskType.SUPPLIER_QUALITY_ANALYSIS_V1
                else (
                    "task:execute",
                    "finance:ap.detail",
                    "finance:ap.artifact:download",
                    "artifact.write",
                )
            ),
            authentication_source="legacy_internal_adapter",
            authenticated=True,
            is_demo_identity=True,
            purpose=contract.task_type.value,
            task_type=contract.task_type,
            output_format=contract.expected_output.artifact_type,
            max_steps=self._max_task_steps,
            read_only=ap_scope.read_only if ap_scope else True,
            require_approval=contract.approval_requirement.required,
            deadline_at=contract.constraints.deadline_at,
            request_source=RequestSource.INTERNAL,
            task_text_hash=hashlib.sha256(request.raw_input.encode("utf-8")).hexdigest(),
            task_text_length=len(request.raw_input),
        )
        self._repository.initialize(
            request,
            contract,
            plan,
            initial,
            tenant_id=contract.constraints.tenant_id,
        )
        owner_id = self._ids.new_id("LEASE")
        self._repository.acquire_execution(
            contract.task_id,
            owner_id,
            tenant_id=contract.constraints.tenant_id,
        )
        config = self._config(contract.task_id, contract.constraints.tenant_id)
        try:
            output = self._invoke_graph(
                initial_graph_state(
                    request=request,
                    intake_context=intake_context,
                    contract=contract,
                    plan=plan,
                    domain_state=initial,
                    started_at=started_at,
                ),
                config,
                intake_context=intake_context,
                resumed=False,
            )
            return self._execution(output)
        finally:
            self._repository.release_execution(
                contract.task_id,
                owner_id,
                tenant_id=contract.constraints.tenant_id,
            )

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
            tenant_id=intake_context.tenant_id,
            task_id=intake_context.task_id,
        )
        self._runtime.record_submission(state)
        owner_id = self._ids.new_id("LEASE")
        self._repository.acquire_execution(
            intake_context.task_id,
            owner_id,
            tenant_id=intake_context.tenant_id,
        )
        config = self._config(intake_context.task_id, intake_context.tenant_id)
        try:
            output = self._invoke_graph(
                state,
                config,
                intake_context=intake_context,
                resumed=False,
            )
            return self._execution(output)
        finally:
            self._repository.release_execution(
                intake_context.task_id,
                owner_id,
                tenant_id=intake_context.tenant_id,
            )

    def resume(self, task_id: str, tenant_id: str) -> WorkflowExecution:
        """Continue from the latest safe checkpoint without replaying successful nodes."""
        config = self._config(task_id, tenant_id)
        snapshot = self._graph.get_state(config)
        if not snapshot.values:
            raise ValueError("workflow checkpoint was not found")
        current = cast(AgentGraphState, snapshot.values)
        if current["task_id"] != task_id or current["intake_context"].tenant_id != tenant_id:
            raise ValueError("workflow checkpoint scope does not match the requested task")
        if current["domain_state"] != self._repository.state_for(task_id, tenant_id=tenant_id):
            raise WorkflowRecoveryError(
                "workflow checkpoint does not match authoritative domain state"
            )
        if current["task_result"] is not None:
            raise ValueError("terminal task cannot be resumed")
        owner_id = self._ids.new_id("LEASE")
        self._repository.acquire_execution(task_id, owner_id, tenant_id=tenant_id)
        try:
            self._graph.update_state(
                config,
                {"resume_count": current["resume_count"] + 1},
            )
            output = self._invoke_graph(
                None,
                config,
                intake_context=current["intake_context"],
                resumed=True,
            )
            return self._execution(output)
        finally:
            self._repository.release_execution(task_id, owner_id, tenant_id=tenant_id)

    def execute_dispatched(
        self,
        request: TaskRequest,
        intake_context: TrustedTaskContext,
        *,
        execution_generation: int,
    ) -> WorkflowExecution:
        """Execute an already-persisted initial Task under a Worker-owned lease."""
        initial = self._repository.state_for(
            intake_context.task_id,
            tenant_id=intake_context.tenant_id,
        )
        if initial.state is not TaskStatus.CREATED:
            raise WorkflowRecoveryError("initial dispatch no longer points at a CREATED Task")
        state = initial_graph_state(
            request=request,
            intake_context=intake_context,
            domain_state=initial,
            started_at=self._clock(),
            execution_generation=execution_generation,
        )
        self._runtime.record_submission(state)
        output = self._invoke_graph(
            state,
            self._config(intake_context.task_id, intake_context.tenant_id),
            intake_context=intake_context,
            resumed=False,
        )
        return self._execution(output)

    def resume_dispatched(
        self,
        task_id: str,
        tenant_id: str,
        *,
        execution_generation: int,
    ) -> WorkflowExecution:
        """Resume a current-generation checkpoint under a Worker-owned lease."""
        config = self._config(task_id, tenant_id)
        snapshot = self._graph.get_state(config)
        if not snapshot.values:
            raise WorkflowRecoveryError("workflow checkpoint was not found")
        current = cast(AgentGraphState, snapshot.values)
        if current["task_id"] != task_id or current["intake_context"].tenant_id != tenant_id:
            raise WorkflowRecoveryError("workflow checkpoint scope does not match the Task")
        if current.get("execution_generation", 1) != execution_generation:
            raise WorkflowRecoveryError("workflow checkpoint execution generation is stale")
        if current["domain_state"] != self._repository.state_for(task_id, tenant_id=tenant_id):
            raise WorkflowRecoveryError(
                "workflow checkpoint does not match authoritative domain state"
            )
        if current["task_result"] is not None:
            raise WorkflowRecoveryError("terminal task cannot be resumed")
        self._graph.update_state(config, {"resume_count": current["resume_count"] + 1})
        output = self._invoke_graph(
            None,
            config,
            intake_context=current["intake_context"],
            resumed=True,
        )
        return self._execution(output)

    def resume_approval_dispatched(
        self,
        approval: ApprovalRequest,
        tenant_id: str,
        *,
        execution_generation: int,
    ) -> WorkflowExecution:
        """Apply a durable approved decision and resume outside the API process."""
        config = self._config(approval.task_id, tenant_id)
        snapshot = self._graph.get_state(config)
        if not snapshot.values:
            raise WorkflowRecoveryError("workflow checkpoint was not found")
        current = cast(AgentGraphState, snapshot.values)
        if (
            current["task_id"] != approval.task_id
            or current["intake_context"].tenant_id != tenant_id
            or current["approval_id"] != approval.approval_id
            or current["approval_step_id"] != approval.step_id
            or current["plan"].planning_version != approval.planning_version
        ):
            raise WorkflowRecoveryError("approval does not match the workflow checkpoint")
        if current["domain_state"].state is not TaskStatus.WAITING_APPROVAL:
            raise WorkflowRecoveryError("checkpoint is not waiting for approval")
        if current.get("execution_generation", 1) != execution_generation - 1:
            raise WorkflowRecoveryError("approval checkpoint is not the immediate predecessor")
        if any(call.step_id == approval.step_id for call in current["tool_calls"]):
            raise WorkflowRecoveryError("approval target tool has already been called")
        authoritative = self._repository.state_for(approval.task_id, tenant_id=tenant_id)
        if authoritative.state is not TaskStatus.EXECUTING:
            raise WorkflowRecoveryError("resolved approval Task is not executable")
        if approval.status is not ApprovalStatus.APPROVED:
            raise WorkflowRecoveryError("only an approved decision may be dispatched")
        self._graph.update_state(
            config,
            {
                "domain_state": authoritative,
                "route": "allowed",
                "route_reason": "Approval resolved; execute the bound step",
                "last_arguments": approval.resolved_arguments,
                "resume_count": current["resume_count"] + 1,
                "execution_generation": execution_generation,
            },
            as_node="policy_check",
        )
        output = self._invoke_graph(
            None,
            config,
            intake_context=current["intake_context"],
            resumed=True,
        )
        return self._execution(output)

    def resume_clarification_dispatched(
        self,
        clarification: TaskClarification,
        refreshed_context: TrustedTaskContext,
        tenant_id: str,
        *,
        execution_generation: int,
    ) -> WorkflowExecution:
        """Resume the suspended checkpoint at task understanding under Worker authority."""
        config = self._config(clarification.task_id, tenant_id)
        snapshot = self._graph.get_state(config)
        if not snapshot.values:
            raise WorkflowRecoveryError("workflow checkpoint was not found")
        current = cast(AgentGraphState, snapshot.values)
        if (
            current["task_id"] != clarification.task_id
            or current["intake_context"].tenant_id != tenant_id
            or current.get("clarification_id") != clarification.clarification_id
            or current.get("clarification_round") != clarification.round
        ):
            raise WorkflowRecoveryError("clarification does not match the workflow checkpoint")
        if current["domain_state"].state is not TaskStatus.WAITING_CLARIFICATION:
            raise WorkflowRecoveryError("checkpoint is not waiting for clarification")
        if current.get("execution_generation", 1) != execution_generation - 1:
            raise WorkflowRecoveryError("clarification checkpoint is not the immediate predecessor")
        if current.get("contract") is not None or current.get("plan") is not None:
            raise WorkflowRecoveryError("clarification checkpoint already contains planning state")
        if clarification.status is not ClarificationStatus.SUBMITTED:
            raise WorkflowRecoveryError("clarification response is not submitted")
        if clarification.response is None or clarification.resume_context is None:
            raise WorkflowRecoveryError("clarification resume payload is incomplete")
        if (
            refreshed_context.task_id != clarification.task_id
            or refreshed_context.tenant_id != tenant_id
            or refreshed_context.task_type is not current["intake_context"].task_type
        ):
            raise WorkflowRecoveryError("refreshed clarification authority is out of scope")
        authoritative = self._repository.state_for(clarification.task_id, tenant_id=tenant_id)
        if authoritative.state is not TaskStatus.UNDERSTANDING:
            raise WorkflowRecoveryError("clarification response Task is not in understanding")
        self._graph.update_state(
            config,
            {
                "domain_state": authoritative,
                "intake_context": refreshed_context,
                "clarification_context": clarification.context,
                "clarification_response": clarification.response,
                "route": "clarification_submitted",
                "route_reason": "Clarification response submitted for validation",
                "resume_count": current["resume_count"] + 1,
                "execution_generation": execution_generation,
            },
            as_node="request_clarification",
        )
        output = self._invoke_graph(
            None,
            config,
            intake_context=refreshed_context,
            resumed=True,
        )
        return self._execution(output)

    def checkpoint_identity(self, task_id: str, tenant_id: str) -> CheckpointIdentity | None:
        """Return minimized tenant-qualified checkpoint facts for reconciliation."""
        snapshot = self._graph.get_state(self._config(task_id, tenant_id))
        if not snapshot.values:
            return None
        current = cast(AgentGraphState, snapshot.values)
        configurable = snapshot.config.get("configurable", {})
        checkpoint_id = configurable.get("checkpoint_id")
        if not isinstance(checkpoint_id, str) or not checkpoint_id:
            raise WorkflowRecoveryError("workflow checkpoint identity is missing")
        plan = current.get("plan")
        return CheckpointIdentity(
            tenant_id=tenant_id,
            task_id=task_id,
            checkpoint_id=checkpoint_id,
            thread_id=f"{tenant_id}:{task_id}",
            task_version=current["domain_state"].version,
            plan_version=plan.planning_version if plan is not None else None,
            execution_generation=current.get("execution_generation", 1),
            current_step_id=current.get("current_step_id"),
            successful_step_ids=tuple(
                result.step_id
                for result in current["step_results"]
                if result.status.value == "SUCCESS"
            ),
        )

    def approval_state(self, task_id: str, tenant_id: str) -> dict[str, object]:
        """Return the minimized checkpoint facts needed to validate one decision."""
        state = self.get_state(task_id, tenant_id)
        identity = self.checkpoint_identity(task_id, tenant_id)
        if identity is None:
            raise ValueError("workflow checkpoint was not found")
        step_id = state["approval_step_id"]
        return {
            "task_status": state["domain_state"].state.value,
            "tenant_id": state["intake_context"].tenant_id,
            "trace_id": state["trace_id"],
            "approval_id": state["approval_id"],
            "step_id": step_id,
            "planning_version": state["plan"].planning_version,
            "target_executed": any(call.step_id == step_id for call in state["tool_calls"]),
            "checkpoint_id": identity.checkpoint_id,
            "execution_generation": identity.execution_generation,
        }

    def clarification_state(self, task_id: str, tenant_id: str) -> dict[str, object]:
        """Return minimized checkpoint facts required for a response CAS."""
        state = self.get_state(task_id, tenant_id)
        identity = self.checkpoint_identity(task_id, tenant_id)
        if identity is None:
            raise ValueError("workflow checkpoint was not found")
        return {
            "task_status": state["domain_state"].state.value,
            "tenant_id": state["intake_context"].tenant_id,
            "trace_id": state["trace_id"],
            "clarification_id": state.get("clarification_id"),
            "clarification_round": state.get("clarification_round", 0),
            "contract_present": state.get("contract") is not None,
            "plan_present": state.get("plan") is not None,
            "checkpoint_id": identity.checkpoint_id,
            "execution_generation": identity.execution_generation,
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
        if current["domain_state"] != self._repository.state_for(
            approval.task_id, tenant_id=tenant_id
        ):
            raise WorkflowRecoveryError(
                "workflow checkpoint does not match authoritative domain state"
            )
        if current["domain_state"].state is not TaskStatus.WAITING_APPROVAL:
            raise WorkflowRecoveryError("task is not waiting for approval")
        if any(call.step_id == approval.step_id for call in current["tool_calls"]):
            raise WorkflowRecoveryError("approval target tool has already been called")
        event = _approval_event(approval)
        owner_id = self._ids.new_id("LEASE")
        self._repository.acquire_execution(approval.task_id, owner_id, tenant_id=tenant_id)
        try:
            domain_state, record = self._state_machine.transition(
                current["domain_state"],
                event,
                reason=f"Approval {approval.approval_id} resolved as {approval.status.value}",
            )
            self._repository.commit_transition(
                current["domain_state"],
                domain_state,
                record,
                tenant_id=tenant_id,
            )
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
            output = self._invoke_graph(
                None,
                config,
                intake_context=current["intake_context"],
                resumed=True,
            )
            return self._execution(output)
        finally:
            self._repository.release_execution(approval.task_id, owner_id, tenant_id=tenant_id)

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

    def _invoke_graph(
        self,
        input_state: AgentGraphState | None,
        config: RunnableConfig,
        *,
        intake_context: TrustedTaskContext,
        resumed: bool,
    ) -> AgentGraphState:
        """Run one task/resume root span and record only terminal task outcome counters."""
        started = self._timer()
        with self._observability.bind_context(
            task_id=intake_context.task_id,
            trace_id=intake_context.trace_id,
            step_id=None,
            node_name=None,
            tool_name=None,
            tenant_id=intake_context.tenant_id,
            user_id=intake_context.user_id,
            session_id=intake_context.session_id,
        ):
            if resumed:
                self._observability.increment("task_resumes_total")
                self._observability.emit(EventName.TASK_RESUMED)
            else:
                self._observability.increment("tasks_started_total")
                self._observability.emit(EventName.TASK_STARTED)
            self._observability.gauge_add("active_tasks", 1)
            try:
                with self._observability.span(
                    "task.total",
                    SpanKind.TASK,
                    attributes={"resume_count": 1 if resumed else 0},
                ) as root_span:
                    try:
                        output = cast(AgentGraphState, self._graph.invoke(input_state, config))
                    except BaseException as exc:
                        root_span.set_status(SpanStatus.FAILED, error_type=type(exc).__name__)
                        self._observability.increment("tasks_failed_total")
                        self._observability.emit(
                            EventName.TASK_FAILED,
                            level=logging.ERROR,
                            fields={"error_type": type(exc).__name__},
                        )
                        raise
                    domain_status = output["domain_state"].state
                    root_span.set_attribute("task_status", domain_status.value)
                    root_span.set_attribute("retry_count", output["tool_retry_count"])
                    root_span.set_attribute("replan_count", output["replan_count"])
                    root_span.set_attribute("approval_count", 1 if output["approval_id"] else 0)
                    if domain_status is TaskStatus.FAILED:
                        root_span.set_status(SpanStatus.FAILED, error_type="TASK_FAILED")
                    elif domain_status is TaskStatus.CANCELLED:
                        root_span.set_status(SpanStatus.CANCELLED)
                    else:
                        root_span.set_status(SpanStatus.SUCCEEDED)
                latency_ms = max(0.0, (self._timer() - started) * 1000)
                if output["task_result"] is not None:
                    self._observability.observe("task_latency_ms", latency_ms)
                    if domain_status is TaskStatus.COMPLETED:
                        self._observability.increment("tasks_completed_total")
                        event = EventName.TASK_COMPLETED
                    elif domain_status is TaskStatus.CANCELLED:
                        self._observability.increment("tasks_cancelled_total")
                        event = EventName.TASK_CANCELLED
                    else:
                        self._observability.increment("tasks_failed_total")
                        event = EventName.TASK_FAILED
                    self._observability.emit(
                        event,
                        level=(
                            logging.INFO if domain_status is TaskStatus.COMPLETED else logging.ERROR
                        ),
                        fields={"status": domain_status.value, "latency_ms": latency_ms},
                    )
                return output
            finally:
                self._observability.gauge_add("active_tasks", -1)

    def _execution(self, state: AgentGraphState) -> WorkflowExecution:
        if state["task_result"] is None:
            raise WorkflowInterrupted(
                state["route_reason"],
                task_id=state["task_id"],
                trace_id=state["trace_id"],
                status=state["domain_state"].state.value,
                created_at=state["request"].created_at,
                approval_id=state["approval_id"],
                clarification_id=state.get("clarification_id"),
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
                self._evidence_reader.get(
                    evidence_id,
                    task_id=state["task_id"],
                    tenant_id=state["intake_context"].tenant_id,
                )
                for evidence_id in state["evidence_ids"]
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
