"""LangGraph checkpoint, restart, and node-trace integration coverage."""

from pathlib import Path
from typing import cast

import pytest

from copilot.agent.graph import WorkflowInterrupted
from copilot.agent.state import AgentGraphState
from copilot.contracts import ApprovalRequirement, TaskState, TaskStatus
from copilot.persistence.identifiers import SequentialIdentifierFactory
from copilot.services.workflows.models import SupplierQualityCommand
from tests.workflow_helpers import FIXED_NOW, TEST_MAX_TASK_STEPS, build_test_container

COMMAND = SupplierQualityCommand(
    supplier_id="SUP-001",
    material_id="MAT-001",
    time_range="2026-Q1",
)


def test_graph_executes_every_explicit_node_and_persists_checkpoint(tmp_path: Path) -> None:
    with build_test_container(tmp_path / "artifacts") as container:
        execution = container.service.execute(COMMAND)
        state = container.engine.get_state(
            execution.task_result.task_id,
            "TENANT-DEMO",
        )
        node_names = {
            record.metadata.root["node_name"]
            for record in container.workflow_audit.list(tenant_id="TENANT-DEMO")
            if record.event == "node_completed"
        }

        assert node_names == {
            "validate_request",
            "understand_task",
            "classify_task",
            "create_plan",
            "validate_plan",
            "policy_check",
            "execute_tool",
            "aggregate_evidence",
            "generate_report",
            "verify_result",
            "persist_result",
        }
        assert state["domain_state"].state is TaskStatus.COMPLETED
        assert state["task_id"] == state["trace_id"]
        assert state["evidence_ids"] == [item.evidence_id for item in execution.evidence]
        assert "evidence" not in state


def test_restart_after_knowledge_does_not_repeat_successful_step(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    ids = SequentialIdentifierFactory()
    with build_test_container(
        artifact_dir,
        ids=ids,
        interrupt_after=("aggregate_evidence",),
    ) as first:
        with pytest.raises(WorkflowInterrupted):
            first.service.execute(COMMAND)
        assert first.knowledge_tool.call_count == 1
        assert first.database_tool.call_count == 0

    with build_test_container(artifact_dir, ids=ids) as restarted:
        execution = restarted.engine.resume("T-0001", "TENANT-DEMO")

        assert execution.task_result.final_status is TaskStatus.COMPLETED
        assert restarted.knowledge_tool.call_count == 0
        assert restarted.database_tool.call_count == 1
        assert restarted.analytics_tool.call_count == 1
        assert restarted.report_tool.call_count == 1
        assert len({item.evidence_id for item in execution.evidence}) == len(execution.evidence)


@pytest.mark.parametrize(
    ("interrupt_after", "interrupt_count", "remaining_tool_calls"),
    [
        ("validate_plan", 1, 4),
        ("aggregate_evidence", 1, 3),
        ("aggregate_evidence", 2, 2),
        ("aggregate_evidence", 3, 1),
        ("generate_report", 1, 0),
        ("verify_result", 1, 0),
    ],
)
def test_restart_from_each_safe_boundary_matches_uninterrupted_result(
    tmp_path: Path,
    interrupt_after: str,
    interrupt_count: int,
    remaining_tool_calls: int,
) -> None:
    baseline_ids = SequentialIdentifierFactory()
    with build_test_container(
        tmp_path / "baseline" / "artifacts",
        ids=baseline_ids,
    ) as baseline_container:
        baseline = baseline_container.service.execute(COMMAND)

    restart_ids = SequentialIdentifierFactory()
    artifact_dir = tmp_path / "restart" / "artifacts"
    with build_test_container(
        artifact_dir,
        ids=restart_ids,
        interrupt_after=(interrupt_after,),
    ) as interrupted:
        with pytest.raises(WorkflowInterrupted):
            interrupted.service.execute(COMMAND)
        for _ in range(interrupt_count - 1):
            with pytest.raises(WorkflowInterrupted):
                interrupted.engine.resume("T-0001", "TENANT-DEMO")

    with build_test_container(artifact_dir, ids=restart_ids) as restarted:
        recovered = restarted.engine.resume("T-0001", "TENANT-DEMO")
        actual_remaining_calls = sum(
            (
                restarted.knowledge_tool.call_count,
                restarted.database_tool.call_count,
                restarted.analytics_tool.call_count,
                restarted.report_tool.call_count,
            )
        )

        assert actual_remaining_calls == remaining_tool_calls
        assert recovered.task_result == baseline.task_result
        assert recovered.step_results[:3] == baseline.step_results[:3]
        recovered_report = recovered.step_results[3]
        baseline_report = baseline.step_results[3]
        assert recovered_report.status is baseline_report.status
        assert recovered_report.evidence == baseline_report.evidence
        assert recovered_report.output is not None
        assert baseline_report.output is not None
        recovered_report_output = dict(recovered_report.output.root)
        baseline_report_output = dict(baseline_report.output.root)
        recovered_report_output.pop("location")
        baseline_report_output.pop("location")
        assert recovered_report_output == baseline_report_output
        assert recovered.verification_result is not None
        assert baseline.verification_result is not None
        assert (
            recovered.verification_result.model_copy(
                update={"duration_ms": baseline.verification_result.duration_ms}
            )
            == baseline.verification_result
        )
        assert recovered.evidence == baseline.evidence
        assert (
            recovered.artifacts[0].model_copy(update={"location": baseline.artifacts[0].location})
            == baseline.artifacts[0]
        )
        assert recovered.task_result.task_id == "T-0001"


def test_terminal_task_cannot_be_resumed(tmp_path: Path) -> None:
    with build_test_container(tmp_path / "artifacts") as container:
        execution = container.service.execute(COMMAND)
        with pytest.raises(ValueError, match="terminal task"):
            container.engine.resume(execution.task_result.task_id, "TENANT-DEMO")


def test_approval_required_stops_before_controlled_database_execution(tmp_path: Path) -> None:
    with build_test_container(
        tmp_path / "seed" / "artifacts",
        interrupt_after=("validate_plan",),
    ) as seed:
        with pytest.raises(WorkflowInterrupted):
            seed.service.execute(COMMAND)
        seeded = seed.engine.get_state("T-0001", "TENANT-DEMO")
    contract = seeded["contract"].model_copy(
        update={
            "approval_requirement": ApprovalRequirement(
                required=True,
                policy_id="quality-approval-v1",
                approver_role="quality_data_approver",
                controlled_scope=("quality.v1",),
            )
        }
    )

    with build_test_container(tmp_path / "approval" / "artifacts") as gated:
        with pytest.raises(WorkflowInterrupted, match="waiting for approval"):
            gated.engine.start(seeded["request"], contract, seeded["plan"])
        state = gated.engine.get_state("T-0001", "TENANT-DEMO")

        assert state["route"] == "interrupted"
        assert state["domain_state"].state is TaskStatus.WAITING_APPROVAL
        assert gated.knowledge_tool.call_count == 1
        assert gated.database_tool.call_count == 0
        assert tuple(
            result.tool_name
            for result in gated.repository.tool_results_for("T-0001", tenant_id="TENANT-DEMO")
        ) == ("knowledge_search",)
        pending = gated.approval_repository.get_pending_for_task("T-0001", tenant_id="TENANT-DEMO")
        assert len(pending) == 1
        assert pending[0].tool_name == "database_query"


def test_missing_information_stops_and_checkpoints_without_defaults(tmp_path: Path) -> None:
    with build_test_container(
        tmp_path / "seed" / "artifacts",
        interrupt_after=("validate_plan",),
    ) as seed:
        with pytest.raises(WorkflowInterrupted):
            seed.service.execute(COMMAND)
        seeded = seed.engine.get_state("T-0001", "TENANT-DEMO")
    constraints = seeded["contract"].constraints.model_copy(update={"data_scope": ()})
    incomplete_contract = seeded["contract"].model_copy(update={"constraints": constraints})

    with build_test_container(tmp_path / "incomplete" / "artifacts") as stopped:
        execution = stopped.engine.start(
            seeded["request"],
            incomplete_contract,
            seeded["plan"],
        )

        assert execution.task_result.final_status is TaskStatus.FAILED
        assert (
            stopped.repository.state_for("T-0001", tenant_id="TENANT-DEMO").state
            is TaskStatus.FAILED
        )
        assert any(
            event.metadata.root.get("route") == "missing_information"
            for event in stopped.workflow_audit.list(tenant_id="TENANT-DEMO")
        )
        assert stopped.checkpoint_connection is not None
        checkpoint_count = stopped.checkpoint_connection.execute(
            "SELECT COUNT(*) FROM checkpoints"
        ).fetchone()
        assert checkpoint_count is not None and checkpoint_count[0] > 0
        assert stopped.repository.tool_results_for("T-0001", tenant_id="TENANT-DEMO") == ()


def test_invalid_plan_never_enters_tool_execution(tmp_path: Path) -> None:
    with build_test_container(
        tmp_path / "seed" / "artifacts",
        interrupt_after=("validate_plan",),
    ) as seed:
        with pytest.raises(WorkflowInterrupted):
            seed.service.execute(COMMAND)
        seeded = seed.engine.get_state("T-0001", "TENANT-DEMO")
    invalid_plan = seeded["plan"].model_copy(update={"steps": ()})

    with build_test_container(tmp_path / "invalid-plan" / "artifacts") as rejected:
        execution = rejected.engine.start(
            seeded["request"],
            seeded["contract"],
            invalid_plan,
        )
        state = rejected.engine.get_state("T-0001", "TENANT-DEMO")

        assert execution.task_result.final_status is TaskStatus.FAILED
        assert any(error.error_code == "PLAN_INVALID" for error in state["errors"])
        assert rejected.repository.tool_results_for("T-0001", tenant_id="TENANT-DEMO") == ()


def test_execution_lease_rejects_concurrent_resume(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    ids = SequentialIdentifierFactory()
    with build_test_container(
        artifact_dir,
        ids=ids,
        interrupt_after=("validate_plan",),
    ) as owner:
        with pytest.raises(WorkflowInterrupted):
            owner.service.execute(COMMAND)
        owner.repository.acquire_execution("T-0001", "OWNER-1", tenant_id="TENANT-DEMO")
        try:
            with build_test_container(artifact_dir, ids=ids) as competitor:
                with pytest.raises(ValueError, match="lease conflict"):
                    competitor.engine.resume("T-0001", "TENANT-DEMO")
                assert competitor.knowledge_tool.call_count == 0
        finally:
            owner.repository.release_execution("T-0001", "OWNER-1", tenant_id="TENANT-DEMO")


@pytest.mark.parametrize(
    ("state_update", "expected_reason"),
    [
        ({"deadline_at": FIXED_NOW}, "deadline"),
        ({"replan_count": 3}, "replan"),
    ],
)
def test_execution_guards_stop_before_tool_execution(
    tmp_path: Path,
    state_update: dict[str, object],
    expected_reason: str,
) -> None:
    with build_test_container(
        tmp_path / "artifacts",
        interrupt_after=("validate_plan",),
    ) as container:
        with pytest.raises(WorkflowInterrupted):
            container.service.execute(COMMAND)
        state = container.engine.get_state("T-0001", "TENANT-DEMO")
        state.update(cast(AgentGraphState, state_update))

        update = container.graph_runtime.policy_check(state)

        assert update["route"] == "deadline_exceeded"
        assert expected_reason in str(update["route_reason"]).lower()
        assert container.knowledge_tool.call_count == 0


def test_max_business_step_guard_stops_before_extra_tool_call(tmp_path: Path) -> None:
    with build_test_container(
        tmp_path / "artifacts",
        interrupt_after=("validate_plan",),
    ) as container:
        with pytest.raises(WorkflowInterrupted):
            container.service.execute(COMMAND)
        state = container.engine.get_state("T-0001", "TENANT-DEMO")
        policy_update = container.graph_runtime.policy_check(state)
        state["domain_state"] = cast(TaskState, policy_update["domain_state"])
        state["current_step_id"] = cast(str, policy_update["current_step_id"])
        state["executed_step_count"] = TEST_MAX_TASK_STEPS

        update = container.graph_runtime.execute_tool(state)

        assert update["route"] == "tool_failure"
        assert "maximum task step" in str(update["route_reason"]).lower()
        assert container.knowledge_tool.call_count == 0
