"""EvidenceLedger append, deduplication, isolation, recovery, and lineage tests."""

from __future__ import annotations

from datetime import timedelta

import pytest

from copilot.contracts import EvidenceItem, EvidenceLedgerSnapshot, EvidenceType
from copilot.evidence.ledger import (
    EvidenceNotFoundError,
    EvidenceValidationError,
    InMemoryEvidenceLedger,
    evidence_fingerprint,
)
from tests.unit.evidence.helpers import DB_ID, TASK_ID, evidence_item, valid_ledger


def test_add_get_list_and_stable_queries() -> None:
    ledger = valid_ledger()

    assert [item.evidence_id for item in ledger.list(TASK_ID)] == [
        "E-DOC-001",
        "E-DB-001",
        "E-CALC-001",
    ]
    assert ledger.get(DB_ID, task_id=TASK_ID).source_type is EvidenceType.DATABASE
    assert [item.evidence_id for item in ledger.find_by_step(TASK_ID, "S-DB")] == [DB_ID]
    assert [item.evidence_id for item in ledger.find_by_type(TASK_ID, EvidenceType.DOCUMENT)] == [
        "E-DOC-001"
    ]
    assert ledger.validate_reference(TASK_ID, DB_ID)


def test_missing_and_cross_task_reference_are_scoped_as_not_found() -> None:
    ledger = valid_ledger()

    with pytest.raises(EvidenceNotFoundError) as missing:
        ledger.get("E-MISSING", task_id=TASK_ID)
    assert missing.value.error.error_code == "EVIDENCE_NOT_FOUND"
    with pytest.raises(EvidenceNotFoundError):
        ledger.get(DB_ID, task_id="T-OTHER")
    assert not ledger.validate_reference("T-OTHER", DB_ID)


@pytest.mark.parametrize(
    ("source_type", "parents"),
    [
        (EvidenceType.DOCUMENT, ()),
        (EvidenceType.DATABASE, ()),
        (EvidenceType.CALCULATION, (DB_ID,)),
    ],
)
def test_type_aware_duplicate_returns_canonical_item(
    source_type: EvidenceType,
    parents: tuple[str, ...],
) -> None:
    ledger = InMemoryEvidenceLedger()
    if parents:
        ledger.add(evidence_item(DB_ID, EvidenceType.DATABASE))
    first = evidence_item("E-FIRST", source_type, parents=parents)
    duplicate = first.model_copy(
        update={
            "evidence_id": "E-SECOND",
            "tool_call_id": "TC-SECOND",
            "timestamp": first.timestamp + timedelta(seconds=10),
        }
    )

    created = ledger.add(first)
    repeated = ledger.add(duplicate)

    assert created.created
    assert not repeated.created
    assert repeated.duplicate_of == first.evidence_id
    expected_count = 2 if parents else 1
    assert len(ledger.list(TASK_ID)) == expected_count
    assert evidence_fingerprint(first) == evidence_fingerprint(duplicate)


def test_identical_content_in_different_tasks_is_not_deduplicated() -> None:
    ledger = InMemoryEvidenceLedger()
    first = evidence_item("E-A", EvidenceType.DOCUMENT, task_id="T-A")
    second = evidence_item("E-B", EvidenceType.DOCUMENT, task_id="T-B")

    assert ledger.add(first).created
    assert ledger.add(second).created
    assert len(ledger.list("T-A")) == len(ledger.list("T-B")) == 1


def test_returned_nested_content_cannot_mutate_ledger_state() -> None:
    ledger = valid_ledger()
    detached = ledger.get(DB_ID, task_id=TASK_ID)
    detached.content.data.root["row_count"] = 999
    listed = ledger.list(TASK_ID)
    listed[1].source_reference.reference.root["query_fingerprint"] = "changed"

    authoritative = ledger.get(DB_ID, task_id=TASK_ID)
    assert authoritative.content.data.root["row_count"] == 2
    assert authoritative.source_reference.reference.root["query_fingerprint"] == "sha256:query"


def test_snapshot_json_round_trip_preserves_order_and_deduplication() -> None:
    ledger = valid_ledger()
    serialized = ledger.snapshot().model_dump_json()
    restored = InMemoryEvidenceLedger.from_snapshot(
        EvidenceLedgerSnapshot.model_validate_json(serialized)
    )

    assert restored.snapshot().model_dump(mode="json") == ledger.snapshot().model_dump(mode="json")
    duplicate = evidence_item("E-DOC-NEW", EvidenceType.DOCUMENT, step_id="S-KB")
    assert not restored.add(duplicate).created


def test_restore_validates_lineage_independent_of_snapshot_item_order() -> None:
    snapshot = valid_ledger().snapshot()
    restored = InMemoryEvidenceLedger.from_snapshot(
        snapshot.model_copy(update={"items": tuple(reversed(snapshot.items))})
    )

    trace = restored.trace_lineage(TASK_ID, "E-CALC-001")
    assert trace.is_complete
    assert trace.ordered_evidence_ids == ("E-CALC-001", DB_ID)


def test_lineage_trace_is_root_first_complete_and_deduplicated() -> None:
    ledger = valid_ledger()
    trace = ledger.trace_lineage(TASK_ID, "E-CALC-001")

    assert trace.is_complete
    assert trace.ordered_evidence_ids == ("E-CALC-001", DB_ID)
    assert [(edge.parent_evidence_id, edge.child_evidence_id) for edge in trace.edges] == [
        (DB_ID, "E-CALC-001")
    ]


def test_multi_parent_lineage_has_stable_lexical_parent_order() -> None:
    ledger = valid_ledger()
    ledger.add(
        evidence_item(
            "E-DB-002",
            EvidenceType.DATABASE,
            step_id="S-DB",
            reference={"query_fingerprint": "sha256:query-2"},
        )
    )
    ledger.add(
        evidence_item(
            "E-CALC-002",
            EvidenceType.CALCULATION,
            parents=("E-DB-002", DB_ID),
        )
    )

    trace = ledger.trace_lineage(TASK_ID, "E-CALC-002")
    assert trace.ordered_evidence_ids == ("E-CALC-002", DB_ID, "E-DB-002")


def test_missing_and_cross_task_lineage_parents_are_rejected() -> None:
    ledger = valid_ledger()
    with pytest.raises(EvidenceValidationError) as missing:
        ledger.add(
            evidence_item(
                "E-CALC-MISSING",
                EvidenceType.CALCULATION,
                parents=("E-NOT-THERE",),
            )
        )
    assert missing.value.error.error_code == "LINEAGE_PARENT_NOT_FOUND"

    ledger.add(evidence_item("E-OTHER", EvidenceType.DATABASE, task_id="T-OTHER"))
    with pytest.raises(EvidenceValidationError) as cross_task:
        ledger.add(
            evidence_item(
                "E-CALC-CROSS",
                EvidenceType.CALCULATION,
                parents=("E-OTHER",),
            )
        )
    assert cross_task.value.error.error_code == "LINEAGE_CROSS_TASK_REFERENCE"


def test_self_reference_and_duplicate_parent_edges_are_rejected() -> None:
    ledger = valid_ledger()
    with pytest.raises(EvidenceValidationError) as self_reference:
        ledger.add(
            evidence_item(
                "E-SELF",
                EvidenceType.CALCULATION,
                parents=("E-SELF",),
            )
        )
    assert self_reference.value.error.error_code == "LINEAGE_SELF_REFERENCE"
    with pytest.raises(EvidenceValidationError) as duplicate:
        ledger.add(
            evidence_item(
                "E-DUPLICATE",
                EvidenceType.CALCULATION,
                parents=(DB_ID, DB_ID),
            )
        )
    assert duplicate.value.error.error_code == "LINEAGE_DUPLICATE_EDGE"


@pytest.mark.parametrize(
    "items",
    [
        (
            evidence_item(
                "E-CYCLE-A",
                EvidenceType.CALCULATION,
                parents=("E-CYCLE-B",),
            ),
            evidence_item(
                "E-CYCLE-B",
                EvidenceType.CALCULATION,
                parents=("E-CYCLE-A",),
            ),
        ),
        (
            evidence_item(
                "E-CYCLE-A",
                EvidenceType.CALCULATION,
                parents=("E-CYCLE-C",),
            ),
            evidence_item(
                "E-CYCLE-B",
                EvidenceType.CALCULATION,
                parents=("E-CYCLE-A",),
            ),
            evidence_item(
                "E-CYCLE-C",
                EvidenceType.CALCULATION,
                parents=("E-CYCLE-B",),
            ),
        ),
    ],
)
def test_two_and_multi_node_cycles_are_rejected_during_restore(
    items: tuple[EvidenceItem, ...],
) -> None:
    snapshot = EvidenceLedgerSnapshot(items=items)

    with pytest.raises(EvidenceValidationError) as cycle:
        InMemoryEvidenceLedger.from_snapshot(snapshot)
    assert cycle.value.error.error_code == "LINEAGE_CYCLE"


def test_calculation_evidence_without_input_is_rejected_by_frozen_contract() -> None:
    with pytest.raises(ValueError, match="calculation evidence must reference input"):
        evidence_item("E-ORPHAN", EvidenceType.CALCULATION)
