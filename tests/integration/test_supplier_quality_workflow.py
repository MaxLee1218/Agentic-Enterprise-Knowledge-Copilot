"""End-to-end deterministic workflow tests using the real governed runtime chain."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from copilot.contracts import (
    EvidenceType,
    StepResultStatus,
    TaskStatus,
    ToolIdempotency,
)
from copilot.services.workflows.models import SupplierQualityCommand
from copilot.tools.mock_supplier_quality import MockBehavior, MockFailureKind
from tests.workflow_helpers import build_test_container

COMMAND = SupplierQualityCommand(
    supplier_id="SUP-001",
    material_id="MAT-001",
    time_range="2026-Q1",
)


def test_success_path_runs_four_tools_and_creates_evidence_backed_report(tmp_path: Path) -> None:
    with build_test_container(tmp_path / "artifacts") as container:
        execution = container.service.execute(COMMAND)

        assert execution.task_result.final_status is TaskStatus.COMPLETED
        assert [result.status for result in execution.step_results] == [
            StepResultStatus.SUCCESS
        ] * 4
        assert [record.attempt_count for record in execution.step_executions] == [1, 1, 1, 1]
        assert all(record.duration_ms >= 0 for record in execution.step_executions)
        assert len(container.repository.tool_results()) == 4
        assert [item.tool_name for item in container.repository.tool_results()] == [
            "knowledge_search",
            "database_query",
            "analysis_engine",
            "report_generator",
        ]
        assert {item.source_type for item in execution.evidence} == {
            EvidenceType.DOCUMENT,
            EvidenceType.DATABASE,
            EvidenceType.CALCULATION,
        }
        database_evidence = next(
            item for item in execution.evidence if item.source_type is EvidenceType.DATABASE
        )
        assert container.analytics_tool.received_evidence_ids == [database_evidence.evidence_id]
        assert set(container.report_tool.received_evidence_ids[0]) == {
            item.evidence_id for item in execution.evidence
        }
        artifact = execution.artifacts[0]
        report_path = Path(artifact.location)
        assert report_path.is_file() and report_path.stat().st_size > 0
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["title"] == "Supplier Quality Analysis Report"
        assert report["scope"]["supplier_ids"] == ["SUP-001"]
        assert {item["evidence_id"] for item in report["evidence_references"]} == {
            item.evidence_id for item in execution.evidence
        }
        audit_events = [item.event for item in container.workflow_audit.list()]
        assert "workflow_started" in audit_events
        assert "artifact_created" in audit_events
        assert audit_events[-1] == "workflow_completed"


def test_database_permanent_failure_blocks_downstream_and_retains_partial_evidence(
    tmp_path: Path,
) -> None:
    behavior = MockBehavior(
        failure_kind=MockFailureKind.PERMANENT,
        always_fail=True,
    )
    with build_test_container(tmp_path, database_behavior=behavior) as container:
        execution = container.service.execute(COMMAND)

        assert execution.task_result.final_status is TaskStatus.FAILED
        assert [result.status for result in execution.step_results] == [
            StepResultStatus.SUCCESS,
            StepResultStatus.TECHNICAL_FAILURE,
            StepResultStatus.CANCELLED,
            StepResultStatus.CANCELLED,
        ]
        assert container.database_tool.call_count == 1
        assert container.analytics_tool.call_count == 0
        assert container.report_tool.call_count == 0
        assert execution.artifacts == ()
        assert all(item.source_type is EvidenceType.DOCUMENT for item in execution.evidence)


def test_first_critical_step_failure_stops_even_independent_unstarted_step(tmp_path: Path) -> None:
    behavior = MockBehavior(
        failure_kind=MockFailureKind.PERMANENT,
        always_fail=True,
    )
    with build_test_container(tmp_path, knowledge_behavior=behavior) as container:
        execution = container.service.execute(COMMAND)
        assert execution.task_result.final_status is TaskStatus.FAILED
        assert len(execution.step_results) == 4
        assert container.knowledge_tool.call_count == 1
        assert container.database_tool.call_count == 0
        assert container.analytics_tool.call_count == 0
        assert container.report_tool.call_count == 0


def test_transient_database_failure_retries_once_then_completes(tmp_path: Path) -> None:
    behavior = MockBehavior(
        failure_kind=MockFailureKind.TRANSIENT,
        fail_first_n_attempts=1,
    )
    with build_test_container(tmp_path, database_behavior=behavior) as container:
        execution = container.service.execute(COMMAND)
        assert execution.task_result.final_status is TaskStatus.COMPLETED
        assert container.database_tool.call_count == 2
        database_record = execution.step_executions[1]
        assert database_record.attempt_count == 2
        assert [item.attempt for item in database_record.attempts] == [1, 2]
        assert any(
            event.status == TaskStatus.RETRYING.value for event in container.workflow_audit.list()
        )


def test_retryable_database_timeout_retries_once_then_completes(tmp_path: Path) -> None:
    behavior = MockBehavior(
        failure_kind=MockFailureKind.TIMEOUT,
        fail_first_n_attempts=1,
    )
    with build_test_container(tmp_path, database_behavior=behavior) as container:
        execution = container.service.execute(COMMAND)
        assert execution.task_result.final_status is TaskStatus.COMPLETED
        assert container.database_tool.call_count == 2
        assert execution.step_executions[1].attempt_count == 2


def test_transient_failure_exhausts_two_retries_and_blocks_downstream(tmp_path: Path) -> None:
    behavior = MockBehavior(
        failure_kind=MockFailureKind.TRANSIENT,
        always_fail=True,
    )
    with build_test_container(tmp_path, database_behavior=behavior) as container:
        execution = container.service.execute(COMMAND)
        assert execution.task_result.final_status is TaskStatus.FAILED
        assert container.database_tool.call_count == 3
        assert execution.step_executions[1].attempt_count == 3
        assert container.analytics_tool.call_count == 0
        assert container.report_tool.call_count == 0


def test_non_idempotent_tool_never_retries_transient_failure(tmp_path: Path) -> None:
    behavior = MockBehavior(
        failure_kind=MockFailureKind.TRANSIENT,
        always_fail=True,
    )
    with build_test_container(tmp_path, database_behavior=behavior) as container:
        current = container.database_tool.definition.idempotency
        container.database_tool.definition = container.database_tool.definition.model_copy(
            update={
                "idempotency": ToolIdempotency(
                    idempotent=False,
                    key_components=current.key_components,
                    reuse_window_seconds=0,
                    side_effects="Non-repeatable controlled test action",
                )
            }
        )
        execution = container.service.execute(COMMAND)
        assert execution.task_result.final_status is TaskStatus.FAILED
        assert container.database_tool.call_count == 1


@pytest.mark.parametrize(
    "failure_kind,expected_status",
    [
        (MockFailureKind.PERMANENT, StepResultStatus.TECHNICAL_FAILURE),
        (MockFailureKind.BUSINESS, StepResultStatus.BUSINESS_FAILURE),
        (MockFailureKind.PERMISSION, StepResultStatus.PERMISSION_DENIED),
    ],
)
def test_non_retryable_failures_are_attempted_once(
    tmp_path: Path,
    failure_kind: MockFailureKind,
    expected_status: StepResultStatus,
) -> None:
    behavior = MockBehavior(failure_kind=failure_kind, always_fail=True)
    with build_test_container(tmp_path, database_behavior=behavior) as container:
        execution = container.service.execute(COMMAND)
        assert execution.step_results[1].status is expected_status
        assert container.database_tool.call_count == 1
        assert container.analytics_tool.call_count == 0


def test_artifact_write_failure_fails_task_without_publishing_artifact(tmp_path: Path) -> None:
    blocked_root = tmp_path / "not-a-directory"
    blocked_root.write_text("occupied", encoding="utf-8")
    with build_test_container(blocked_root) as container:
        execution = container.service.execute(COMMAND)
        assert execution.task_result.final_status is TaskStatus.FAILED
        assert execution.step_results[-1].status is StepResultStatus.TECHNICAL_FAILURE
        assert execution.artifacts == ()
        assert execution.task_result.artifacts == ()


def test_workflow_audit_failure_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with build_test_container(tmp_path) as container:

        def fail_audit(_record: object) -> None:
            raise OSError("controlled audit outage")

        monkeypatch.setattr(container.workflow_audit, "append", fail_audit)
        with pytest.raises(OSError, match="audit outage"):
            container.service.execute(COMMAND)
        assert container.knowledge_tool.call_count == 0
