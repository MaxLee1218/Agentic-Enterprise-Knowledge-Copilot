"""Direct ToolExecutor policy, context, and exact-approval enforcement tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from copilot.contracts import (
    ApprovalRequest,
    ApprovalResolutionAction,
    ApprovalStatus,
    JsonObject,
    ToolCall,
    ToolResultStatus,
)
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.persistence.approval_repository import ApprovalRepository
from copilot.persistence.audit_repository import ToolAuditRepository
from copilot.policies.approval import action_fingerprint, schema_fingerprint
from copilot.policies.offline import OfflineSupplierQualityAuthorizer
from copilot.tools import ToolExecutor, ToolRegistry
from copilot.tools.exceptions import ToolAuthorizationError
from copilot.tools.knowledge import KnowledgeTool, MockKnowledgeClient
from tests.execution_helpers import execution_context

NOW = datetime(2026, 8, 8, 8, 0, tzinfo=UTC)
TENANT = "TENANT-A"


def _arguments(*, top_k: int = 10) -> JsonObject:
    return JsonObject(
        {
            "query": "quality policy",
            "tenant_id": TENANT,
            "collection_ids": ["quality"],
            "supplier_ids": ["SUP-001"],
            "date_range": {"start": "2026-01-01", "end": "2026-03-31"},
            "top_k": top_k,
            "index_snapshot_id": "snapshot-1",
        }
    )


def _call(*, approval_id: str | None, arguments: JsonObject | None = None) -> ToolCall:
    return ToolCall(
        tool_call_id=f"TC-{approval_id or 'NONE'}",
        task_id="T-APPROVAL",
        step_id="S-KNOWLEDGE",
        tool_name=KnowledgeTool.definition.tool_name,
        tool_version=KnowledgeTool.definition.tool_version,
        input=arguments or _arguments(),
        idempotency_key=f"IDEMPOTENCY-{approval_id or 'NONE'}",
        approval_id=approval_id,
        deadline_at=NOW + timedelta(minutes=5),
        tenant_id=TENANT,
        user_id="U-REQUESTER",
    )


def _approval(
    *,
    approval_id: str,
    status: ApprovalStatus = ApprovalStatus.APPROVED,
    task_id: str = "T-APPROVAL",
    tenant_id: str = TENANT,
    tool_name: str = KnowledgeTool.definition.tool_name,
    proposed: JsonObject | None = None,
) -> tuple[ApprovalRequest, ApprovalRequest]:
    arguments = proposed or _arguments()
    schema_digest = schema_fingerprint(KnowledgeTool.definition)
    fingerprint = action_fingerprint(
        task_id=task_id,
        planning_version=1,
        step_id="S-KNOWLEDGE",
        tool_name=tool_name,
        tool_version=KnowledgeTool.definition.tool_version,
        input_schema_fingerprint=schema_digest,
        controlled_scope=("quality.v1",),
        arguments=arguments,
    )
    pending = ApprovalRequest(
        approval_id=approval_id,
        task_id=task_id,
        tenant_id=tenant_id,
        step_id="S-KNOWLEDGE",
        planning_version=1,
        tool_name=tool_name,
        tool_version=KnowledgeTool.definition.tool_version,
        input_schema_fingerprint=schema_digest,
        original_action_fingerprint=fingerprint,
        controlled_scope=("quality.v1",),
        editable_fields=("top_k",),
        proposed_arguments=arguments,
        reason="Controlled retrieval approval test",
        requester="U-REQUESTER",
        required_role="quality_data_approver",
        status=ApprovalStatus.PENDING,
        policy_version="quality-policy.v1",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    if status is ApprovalStatus.APPROVED:
        resolved = pending.model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "resolution_action": ApprovalResolutionAction.APPROVE,
                "resolved_arguments": arguments,
                "resolved_action_fingerprint": fingerprint,
                "approver": "U-APPROVER",
                "decided_at": NOW + timedelta(minutes=1),
                "version": 2,
            }
        )
    else:
        resolved = pending.model_copy(
            update={
                "status": ApprovalStatus.REJECTED,
                "resolution_action": ApprovalResolutionAction.REJECT,
                "resolution_reason": "Rejected by test approver",
                "approver": "U-APPROVER",
                "decided_at": NOW + timedelta(minutes=1),
                "version": 2,
            }
        )
    return pending, resolved


def _runtime(
    approvals: ApprovalRepository,
) -> tuple[ToolExecutor, MockKnowledgeClient, ToolAuditRepository]:
    client = MockKnowledgeClient()
    registry = ToolRegistry()
    registry.register(KnowledgeTool(client))
    audit = ToolAuditRepository()
    return (
        ToolExecutor(
            registry=registry,
            authorizer=OfflineSupplierQualityAuthorizer(
                approvals,
                clock=lambda: NOW + timedelta(minutes=2),
            ),
            evidence_recorder=InMemoryEvidenceLedger(clock=lambda: NOW),
            audit_sink=audit,
            clock=lambda: NOW + timedelta(minutes=2),
        ),
        client,
        audit,
    )


def _store(
    repository: ApprovalRepository,
    pair: tuple[ApprovalRequest, ApprovalRequest],
) -> None:
    pending, resolved = pair
    repository.create(pending, tenant_id=pending.tenant_id)
    repository.resolve(pending, resolved, tenant_id=pending.tenant_id)


def test_missing_context_mismatch_and_policy_denial_fail_before_adapter_execution() -> None:
    approvals = ApprovalRepository()
    executor, client, _audit = _runtime(approvals)
    call = _call(approval_id=None)
    try:
        with pytest.raises(TypeError):
            executor.execute(call)  # type: ignore[call-arg]
        wrong_tenant = replace(execution_context(call), tenant_id="TENANT-B")
        with pytest.raises(ToolAuthorizationError, match="exact invocation"):
            executor.execute(call, wrong_tenant)
        denied = executor.execute(
            call,
            execution_context(call, roles=("unrecognized_role",)),
        )
    finally:
        executor.close()
    assert denied.status is ToolResultStatus.PERMISSION_DENIED
    assert client.ask_call_count == 0


def test_required_approval_missing_or_unknown_is_denied() -> None:
    approvals = ApprovalRepository()
    executor, client, _audit = _runtime(approvals)
    try:
        missing = _call(approval_id=None)
        no_approval = executor.execute(
            missing,
            execution_context(missing, approval_required=True),
        )
        unknown = _call(approval_id="AP-UNKNOWN")
        wrong_id = executor.execute(
            unknown,
            execution_context(unknown, approval_required=True),
        )
    finally:
        executor.close()
    assert no_approval.error is not None and no_approval.error.error_code == "APPROVAL_REQUIRED"
    assert wrong_id.status is ToolResultStatus.PERMISSION_DENIED
    assert client.ask_call_count == 0


@pytest.mark.parametrize(
    ("approval_kwargs", "call_arguments"),
    [
        ({"task_id": "T-OTHER"}, _arguments()),
        ({"tenant_id": "TENANT-B"}, _arguments()),
        ({"tool_name": "database_query"}, _arguments()),
        ({"proposed": _arguments(top_k=10)}, _arguments(top_k=5)),
        ({"status": ApprovalStatus.REJECTED}, _arguments()),
    ],
)
def test_approval_from_wrong_task_tenant_tool_arguments_or_status_is_denied(
    approval_kwargs: dict[str, object],
    call_arguments: JsonObject,
) -> None:
    approvals = ApprovalRepository()
    approval_id = "AP-MISMATCH"
    _store(approvals, _approval(approval_id=approval_id, **approval_kwargs))  # type: ignore[arg-type]
    executor, client, _audit = _runtime(approvals)
    call = _call(approval_id=approval_id, arguments=call_arguments)
    try:
        result = executor.execute(call, execution_context(call, approval_required=True))
    finally:
        executor.close()
    assert result.status is ToolResultStatus.PERMISSION_DENIED
    assert client.ask_call_count == 0


def test_exact_approved_action_executes_and_audit_contains_context_without_arguments() -> None:
    approvals = ApprovalRepository()
    approval_id = "AP-MATCHING"
    _store(approvals, _approval(approval_id=approval_id))
    executor, client, audit = _runtime(approvals)
    call = _call(approval_id=approval_id)
    secret = "stage17-audit-secret-value"
    try:
        result = executor.execute(
            call,
            execution_context(
                call,
                approval_required=True,
                scopes=("quality.read", f"access_token={secret}"),
            ),
        )
    finally:
        executor.close()

    assert result.status is ToolResultStatus.SUCCESS
    assert client.ask_call_count == 1
    record = audit.list(tenant_id=TENANT)[0]
    assert record.trace_id == f"TRACE-{call.tool_call_id}"
    assert record.approval_id == approval_id
    assert record.arguments_hash is not None
    assert record.tool_origin == "local:copilot"
    assert record.tool_provenance == f"copilot:{KnowledgeTool.definition.tool_version}"
    assert "quality policy" not in repr(record)
    assert secret not in repr(record)
