"""Opt-in real PostgreSQL seed and governed DatabaseTool integration coverage."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, inspect, text
from sqlalchemy.exc import DBAPIError

from copilot.contracts import EvidenceType, JsonObject, ToolCall
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.services.task_intake import (
    NaturalLanguageTaskCommand,
    RequestSource,
    TaskOutputFormat,
    TrustedCallerContext,
)
from copilot.tools.base import ToolExecutionContext
from copilot.tools.database import DatabaseConnection, DatabaseTool
from copilot.tools.database.ap_seed import seed_accounts_payable_demo_database
from copilot.tools.database.migrations import (
    BUSINESS_SCHEMA_BASELINE_REVISION,
    BUSINESS_SCHEMA_HEAD_REVISION,
    downgrade_business_schema,
    upgrade_business_schema,
)
from copilot.tools.database.models import Supplier
from copilot.tools.database.seed import TARGET_INSPECTION_COUNT, seed_demo_database
from tests.unit.database.helpers import database_arguments, database_context
from tests.workflow_helpers import build_test_container

POSTGRES_SEED_URL = os.getenv("TEST_BUSINESS_POSTGRES_URL")
POSTGRES_READONLY_URL = os.getenv("TEST_BUSINESS_POSTGRES_READONLY_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not POSTGRES_SEED_URL,
        reason="TEST_BUSINESS_POSTGRES_URL is not configured for an isolated demo database",
    ),
]


def test_postgres_ap_migration_seed_and_rollback_round_trip() -> None:
    """Exercise the separate AP business history against an explicitly isolated PostgreSQL DB."""
    assert POSTGRES_SEED_URL is not None
    quality_report = seed_demo_database(POSTGRES_SEED_URL, reset=True)
    ap_report = seed_accounts_payable_demo_database(POSTGRES_SEED_URL, reset=True)
    engine = create_engine(POSTGRES_SEED_URL)
    try:
        assert ap_report.invoice_count == 27
        assert "payments" in inspect(engine).get_table_names()

        downgrade_business_schema(POSTGRES_SEED_URL, BUSINESS_SCHEMA_BASELINE_REVISION)
        assert "payments" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM suppliers")).scalar_one() == 17
            assert (
                connection.execute(text("SELECT count(*) FROM incoming_inspections")).scalar_one()
                == quality_report.inspection_count
            )

        upgrade_business_schema(POSTGRES_SEED_URL)
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM business_schema_version")
                ).scalar_one()
                == BUSINESS_SCHEMA_HEAD_REVISION
            )
    finally:
        engine.dispose()


def test_postgres_seed_and_database_tool_round_trip() -> None:
    """Reset only an explicitly isolated business DB, then query it through the real tool."""
    assert POSTGRES_SEED_URL is not None
    report = seed_demo_database(POSTGRES_SEED_URL, reset=True)
    tool = DatabaseTool(
        DatabaseConnection(POSTGRES_READONLY_URL or POSTGRES_SEED_URL, read_only=True)
    )
    try:
        result = tool.execute(
            database_arguments(
                supplier_ids=["SUP-005"],
                start_date="2026-07-01",
                end_date="2026-09-30",
            ),
            database_context(),
        )
    finally:
        tool.close()

    assert report.inspection_count == TARGET_INSPECTION_COUNT
    assert result.output.root["row_count"] == 3
    assert result.output.root["empty_result"] is False
    assert result.evidence[0].source_reference.reference.root["read_only"] is True


def test_postgres_ap_templates_match_sqlite_results(tmp_path: Path) -> None:
    """Prove the five trusted AP reads have driver-neutral rows and fingerprints."""
    assert POSTGRES_SEED_URL is not None
    sqlite_url = f"sqlite:///{tmp_path / 'ap-parity.db'}"
    seed_accounts_payable_demo_database(sqlite_url, reset=True)
    seed_accounts_payable_demo_database(POSTGRES_SEED_URL, reset=True)
    sqlite_tool = DatabaseTool.accounts_payable(DatabaseConnection(sqlite_url, read_only=True))
    postgres_tool = DatabaseTool.accounts_payable(
        DatabaseConnection(POSTGRES_READONLY_URL or POSTGRES_SEED_URL, read_only=True)
    )
    templates = (
        "ap_invoice_population_v1",
        "ap_duplicate_invoice_candidates_v1",
        "ap_invoice_po_variance_v1",
        "ap_payment_terms_v1",
        "ap_payment_amount_v1",
    )
    try:
        for index, template_id in enumerate(templates, start=1):
            arguments = _ap_arguments(template_id)
            sqlite_result = sqlite_tool.execute(
                arguments,
                _ap_context(sqlite_tool, arguments, suffix=f"SQLITE-{index}"),
            )
            postgres_result = postgres_tool.execute(
                arguments,
                _ap_context(postgres_tool, arguments, suffix=f"POSTGRES-{index}"),
            )
            assert sqlite_result.output == postgres_result.output
            assert sqlite_result.evidence[0].content.checksum == (
                postgres_result.evidence[0].content.checksum
            )
    finally:
        sqlite_tool.close()
        postgres_tool.close()


@pytest.mark.skipif(
    not POSTGRES_READONLY_URL,
    reason="TEST_BUSINESS_POSTGRES_READONLY_URL is not configured",
)
def test_postgres_runtime_identity_rejects_business_writes() -> None:
    """Prove that the Compose-style runtime identity has no INSERT privilege."""
    assert POSTGRES_READONLY_URL is not None
    engine = create_engine(POSTGRES_READONLY_URL)
    try:
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(
                insert(Supplier).values(
                    id=99_999,
                    tenant_id="TENANT-DEMO",
                    supplier_code="SUP-DENIED",
                    name="Denied Runtime Write",
                    country="CN",
                    category="Security Test",
                    risk_level="LOW",
                )
            )
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not POSTGRES_READONLY_URL,
    reason="TEST_BUSINESS_POSTGRES_READONLY_URL is not configured",
)
def test_postgres_natural_language_agent_workflow_reaches_verified_report(
    tmp_path: Path,
) -> None:
    """Run the real Agent chain with PostgreSQL business data and mock knowledge only."""
    assert POSTGRES_SEED_URL is not None
    assert POSTGRES_READONLY_URL is not None
    seed_demo_database(POSTGRES_SEED_URL, reset=True)
    caller = TrustedCallerContext(
        user_id="U-POSTGRES-SMOKE",
        tenant_id="TENANT-DEMO",
        data_scope=("quality.v1", "supplier-quality-policy-v1"),
    )
    with build_test_container(
        tmp_path / "postgres-agent-artifacts",
        database_url=POSTGRES_READONLY_URL,
        use_real_database=True,
        llm_provider=OfflineMockLLM(),
    ) as container:
        execution = container.task_service.submit(
            NaturalLanguageTaskCommand(
                task=(
                    "Analyze SUP-005 supplier quality for Q3 2026, compare it with the quality "
                    "policy, and generate a JSON management report."
                ),
                output_format=TaskOutputFormat.JSON,
                source=RequestSource.API,
                trace_id="TRACE-POSTGRES-BUSINESS-SMOKE",
            ),
            caller,
        )

    assert execution.task_result.final_status.value == "COMPLETED"
    assert execution.step_results[1].output is not None
    assert execution.step_results[1].output.root["row_count"] == 3
    assert execution.step_results[2].output is not None
    assert execution.step_results[2].output.root["empty_result"] is False
    assert execution.verification_result is not None
    assert execution.verification_result.status.value == "PASSED"
    assert {item.source_type for item in execution.evidence} == {
        EvidenceType.DOCUMENT,
        EvidenceType.DATABASE,
        EvidenceType.CALCULATION,
    }


def _ap_arguments(template_id: str) -> JsonObject:
    return JsonObject(
        {
            "query_template_id": template_id,
            "parameters": {
                "tenant_id": "TENANT-DEMO",
                "start_date": "2026-04-01",
                "end_date": "2026-06-30",
                "supplier_ids": [],
                "legal_entity_ids": ["LE-CN-01", "LE-US-01"],
                "business_unit_ids": [],
                "currency_scope": [],
            },
            "schema_version": "accounts_payable.v1",
            "snapshot_at": "2026-10-01T00:00:00+00:00",
            "row_limit": 50000,
        }
    )


def _ap_context(
    tool: DatabaseTool,
    arguments: JsonObject,
    *,
    suffix: str,
) -> ToolExecutionContext:
    return ToolExecutionContext(
        call=ToolCall(
            tool_call_id=f"TC-AP-{suffix}",
            task_id="T-AP-POSTGRES-PARITY",
            step_id=f"S-AP-{suffix}",
            tool_name="database_query",
            tool_version=tool.definition.tool_version,
            input=arguments,
            idempotency_key=f"IDEMPOTENCY-AP-{suffix}",
            approval_id=None,
            deadline_at=datetime.now(UTC) + timedelta(seconds=20),
            tenant_id="TENANT-DEMO",
            user_id="U-FINANCE-PARITY",
        ),
        roles=("finance_analyst",),
        scopes=("finance:ap.detail",),
        purpose="accounts_payable_analysis.v1",
    )
