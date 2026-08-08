"""Database Tool success, failure, and Evidence behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from copilot.contracts import EvidenceType
from copilot.tools.database.connection import DatabaseConnection
from copilot.tools.database.errors import (
    DatabaseConnectionError,
    DatabaseStatementTimeoutError,
)
from copilot.tools.database.tool import DatabaseTool
from copilot.tools.exceptions import (
    ToolBusinessError,
    ToolExecutionError,
    ToolPermissionError,
    ToolTimeoutError,
)
from tests.unit.database.helpers import (
    database_arguments,
    database_context,
    seeded_tool,
)


def test_valid_select_returns_frozen_output_and_minimized_evidence(tmp_path: Path) -> None:
    tool = seeded_tool(tmp_path)
    try:
        result = tool.execute(database_arguments(), database_context())
    finally:
        tool.close()

    assert result.output.root["row_count"] == 3
    assert result.output.root["empty_result"] is False
    assert result.output.root["truncated"] is False
    assert "query_fingerprint" in result.output.root
    rows = result.output.root["rows"]
    assert isinstance(rows, list)
    first_row = rows[0]
    assert isinstance(first_row, dict)
    assert first_row["supplier_id"] == "SUP-001"
    evidence = result.evidence[0]
    assert evidence.source_type is EvidenceType.DATABASE
    assert evidence.source_reference.reference.root["query_template_id"] == (
        "supplier_quality_summary_v1"
    )
    assert "query_fingerprint" in evidence.source_reference.reference.root
    assert "sql" not in evidence.source_reference.reference.root
    assert "rows" not in evidence.content.data.root


def test_empty_result_is_success_with_database_evidence(tmp_path: Path) -> None:
    tool = seeded_tool(tmp_path)
    try:
        result = tool.execute(
            database_arguments(supplier_ids=["SUP-NOT-FOUND"]),
            database_context(),
        )
    finally:
        tool.close()

    assert result.output.root["rows"] == []
    assert result.output.root["row_count"] == 0
    assert result.output.root["empty_result"] is True
    assert len(result.evidence) == 1


def test_row_limit_truncates_without_exceeding_output_contract(tmp_path: Path) -> None:
    tool = seeded_tool(tmp_path)
    try:
        result = tool.execute(
            database_arguments(supplier_ids=[], row_limit=1),
            database_context(),
        )
    finally:
        tool.close()

    assert result.output.root["row_count"] == 1
    assert result.output.root["truncated"] is True


def test_supplier_identifier_is_bound_as_data_not_executed_as_sql(tmp_path: Path) -> None:
    tool = seeded_tool(tmp_path)
    try:
        result = tool.execute(
            database_arguments(supplier_ids=["SUP-001') OR 1=1 --"]),
            database_context(),
        )
    finally:
        tool.close()

    assert result.output.root["row_count"] == 0


def test_semantic_scope_and_schema_violations_fail_closed(tmp_path: Path) -> None:
    tool = seeded_tool(tmp_path)
    try:
        with pytest.raises(ToolPermissionError):
            tool.execute(
                database_arguments(start_date="2026-04-01", end_date="2026-03-31"),
                database_context(),
            )
        with pytest.raises(ToolPermissionError):
            tool.execute(
                database_arguments(tenant_id="TENANT-A"),
                database_context(tenant_id="TENANT-DEMO"),
            )
        with pytest.raises(ToolBusinessError):
            tool.execute(
                database_arguments(schema_version="quality.v2"),
                database_context(),
            )
    finally:
        tool.close()


def test_statement_timeout_and_connection_error_map_to_typed_tool_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = seeded_tool(tmp_path)
    try:
        monkeypatch.setattr(
            tool._connection,  # noqa: SLF001 - controlled adapter-boundary test
            "execute_select",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                DatabaseStatementTimeoutError("controlled timeout")
            ),
        )
        with pytest.raises(ToolTimeoutError) as timeout:
            tool.execute(database_arguments(), database_context())
        assert timeout.value.error.error_code == "DATABASE_TIMEOUT"

        monkeypatch.setattr(
            tool._connection,  # noqa: SLF001 - controlled adapter-boundary test
            "execute_select",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                DatabaseConnectionError("controlled connection error")
            ),
        )
        with pytest.raises(ToolExecutionError) as unavailable:
            tool.execute(database_arguments(), database_context())
        assert unavailable.value.error.error_code == "DATABASE_UNAVAILABLE"
    finally:
        tool.close()


def test_missing_database_is_reported_as_unavailable(tmp_path: Path) -> None:
    connection = DatabaseConnection(f"sqlite:///{tmp_path / 'missing.db'}", read_only=True)
    tool = DatabaseTool(connection)
    try:
        with pytest.raises(ToolExecutionError) as unavailable:
            tool.execute(database_arguments(), database_context())
        assert unavailable.value.error.error_code == "DATABASE_UNAVAILABLE"
    finally:
        tool.close()


def test_business_database_readiness_checks_registered_schema(tmp_path: Path) -> None:
    ready_tool = seeded_tool(tmp_path)
    missing_tool = DatabaseTool(
        DatabaseConnection(f"sqlite:///{tmp_path / 'missing-readiness.db'}", read_only=True)
    )
    try:
        assert ready_tool.check_ready() is True
        assert missing_tool.check_ready() is False
    finally:
        ready_tool.close()
        missing_tool.close()
