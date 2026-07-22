"""Controlled builders shared by deterministic workflow tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from copilot.bootstrap.container import WorkflowContainer, build_workflow_container
from copilot.config import Settings
from copilot.persistence.identifiers import SequentialIdentifierFactory
from copilot.tools.mock_supplier_quality import MockBehavior

FIXED_NOW = datetime(2026, 7, 22, 8, 0, tzinfo=UTC)


def fixed_clock() -> datetime:
    """Return one stable UTC instant for deterministic content assertions."""
    return FIXED_NOW


def build_test_container(
    artifact_dir: Path,
    *,
    knowledge_behavior: MockBehavior | None = None,
    database_behavior: MockBehavior | None = None,
    analytics_behavior: MockBehavior | None = None,
    report_behavior: MockBehavior | None = None,
) -> WorkflowContainer:
    """Compose the real runner/runtime with offline adapters and no real waiting."""
    settings = Settings(
        database_url="sqlite:///unused-test.db",
        artifact_dir=artifact_dir,
        workflow_max_retries=2,
        workflow_retry_delay_seconds=0,
    )
    return build_workflow_container(
        settings,
        ids=SequentialIdentifierFactory(),
        clock=fixed_clock,
        sleeper=lambda _seconds: None,
        knowledge_behavior=knowledge_behavior,
        database_behavior=database_behavior,
        analytics_behavior=analytics_behavior,
        report_behavior=report_behavior,
    )
