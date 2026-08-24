"""Cross-domain prebuilt Plans must fail before any business tool invocation."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from copilot.contracts import TaskRequest, TaskStatus
from tests.unit.domain.ap_helpers import make_ap_contract
from tests.unit.domain.helpers import make_plan
from tests.workflow_helpers import build_test_container


def test_prebuilt_ap_contract_rejects_quality_plan_without_tool_calls(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    base_contract = make_ap_contract()
    contract = base_contract.model_copy(
        update={
            "constraints": base_contract.constraints.model_copy(
                update={"deadline_at": now + timedelta(minutes=5)}
            ),
            "created_at": now,
        }
    )
    quality_plan = make_plan()
    plan = quality_plan.model_copy(
        update={
            "task_id": contract.task_id,
            "steps": tuple(
                step.model_copy(update={"task_id": contract.task_id}) for step in quality_plan.steps
            ),
        }
    )
    request = TaskRequest(
        id="R-AP-001",
        user_id="U-AP-001",
        raw_input="Analyze Accounts Payable compliance exceptions",
        created_at=contract.created_at,
    )

    with build_test_container(tmp_path / "artifacts") as container:
        execution = container.engine.start(request, contract, plan)
        state = container.engine.get_state(contract.task_id, contract.constraints.tenant_id)

        assert execution.final_state.state is TaskStatus.FAILED
        assert any(error.error_code == "PLAN_INVALID" for error in state["errors"])
        assert container.tool_audit.list(tenant_id=contract.constraints.tenant_id) == ()
        assert (
            container.repository.tool_results_for(
                contract.task_id,
                tenant_id=contract.constraints.tenant_id,
            )
            == ()
        )
