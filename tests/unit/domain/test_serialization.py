"""End-to-end JSON restoration tests for the frozen traceability chain."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from copilot.contracts import (
    Artifact,
    ArtifactType,
    EvidenceContent,
    EvidenceItem,
    EvidenceSourceReference,
    EvidenceType,
    JsonObject,
    TaskConstraints,
    TaskRequest,
)
from tests.unit.domain.helpers import TASK_ID, make_constraints, make_contract, make_plan


def test_core_traceability_models_round_trip_through_json() -> None:
    """Request, contract, plan, evidence, and artifact should restore exactly."""
    request = TaskRequest(
        id="R-001",
        user_id="U-QUALITY-01",
        raw_input="Analyze suppliers S-100 and S-200 in Q1 2026",
        metadata=JsonObject({"locale": "zh-CN"}),
    )
    contract = make_contract()
    plan = make_plan()
    evidence = EvidenceItem(
        evidence_id="E-DB-01",
        task_id=TASK_ID,
        step_id="S-DB-01",
        tool_call_id="TC-DB-01",
        source_type=EvidenceType.DATABASE,
        source_reference=EvidenceSourceReference(
            reference=JsonObject(
                {"query_fingerprint": "sha256:query", "snapshot_at": "2026-07-19T08:00:00Z"}
            )
        ),
        content=EvidenceContent(
            data=JsonObject({"row_count": 6}),
            classification="CONFIDENTIAL",
            checksum="sha256:dataset",
        ),
        timestamp=datetime(2026, 7, 19, 8, 0, tzinfo=UTC),
    )
    artifact = Artifact(
        artifact_id="A-001",
        task_id=TASK_ID,
        type=ArtifactType.QUALITY_ANALYSIS_REPORT_PDF,
        location="artifact://TENANT-A/T-001/A-001",
        media_type="application/pdf",
        checksum="sha256:artifact",
        size_bytes=4096,
        generator_version="supplier_quality_report.v1",
        evidence_ids=(evidence.evidence_id,),
        created_at=datetime(2026, 7, 19, 8, 5, tzinfo=UTC),
    )

    models = (request, contract, plan, evidence, artifact)
    for model in models:
        restored = type(model).model_validate_json(model.model_dump_json())
        assert restored == model


def test_calculation_evidence_requires_input_lineage() -> None:
    """A calculated claim cannot be registered without source evidence."""
    with pytest.raises(ValidationError, match="must reference input evidence"):
        EvidenceItem(
            evidence_id="E-CALC-01",
            task_id=TASK_ID,
            step_id="S-AN-01",
            tool_call_id="TC-AN-01",
            source_type=EvidenceType.CALCULATION,
            source_reference=EvidenceSourceReference(
                reference=JsonObject({"algorithm_version": "quality_metrics.v1"})
            ),
            content=EvidenceContent(
                data=JsonObject({"defect_rate": 0.015}),
                classification="CONFIDENTIAL",
                checksum="sha256:calculation",
            ),
            timestamp=datetime(2026, 7, 19, 8, 1, tzinfo=UTC),
        )


def test_old_json_restores_when_new_optional_field_is_absent() -> None:
    """Adding an optional defaulted field must not break previously persisted JSON."""
    old_payload = make_constraints().model_dump(mode="json")
    old_payload.pop("max_cost")

    restored = TaskConstraints.model_validate(old_payload)

    assert restored.max_cost is None


def test_unknown_future_field_is_rejected_until_contract_version_changes() -> None:
    """Unversioned future fields must not silently contaminate a frozen contract."""
    payload = make_constraints().model_dump(mode="json")
    payload["future_scope_override"] = "all-tenants"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TaskConstraints.model_validate(payload)
