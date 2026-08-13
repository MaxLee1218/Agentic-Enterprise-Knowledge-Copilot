"""Persistence engine and transaction lifecycle tests."""

from pathlib import Path

import pytest
from sqlalchemy import Table, UniqueConstraint, select

from copilot.persistence.database import PersistenceDatabase, PersistenceSchemaError
from copilot.persistence.models import WorkflowApprovalRow, WorkflowStepResultRow


def test_step_result_identity_is_scoped_to_tenant_and_task() -> None:
    table = WorkflowStepResultRow.__table__
    assert isinstance(table, Table)
    unique_column_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("tenant_id", "task_id", "step_id") in unique_column_sets
    assert ("step_id",) not in unique_column_sets


def test_session_rolls_back_and_remains_usable_after_failure(tmp_path: Path) -> None:
    database = PersistenceDatabase.from_sqlite_path(tmp_path / "copilot.db")
    database.create_schema_for_tests()
    try:
        with pytest.raises(RuntimeError, match="forced"), database.session() as session:
            session.add(
                WorkflowApprovalRow(
                    approval_id="AP-ROLLBACK",
                    task_id="T-001",
                    step_id="S-001",
                    status="PENDING",
                    version=1,
                    payload_json="{}",
                )
            )
            raise RuntimeError("forced")

        with database.session() as session:
            assert session.scalar(select(WorkflowApprovalRow.approval_id)) is None
        database.ping()
    finally:
        database.dispose()


def test_schema_validation_fails_before_automatic_creation(tmp_path: Path) -> None:
    database = PersistenceDatabase.from_sqlite_path(tmp_path / "empty.db")
    try:
        with pytest.raises(PersistenceSchemaError, match="migration is required"):
            database.require_schema()
    finally:
        database.dispose()
