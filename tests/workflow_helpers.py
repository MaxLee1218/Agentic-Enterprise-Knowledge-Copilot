"""Controlled builders shared by deterministic workflow tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from copilot.bootstrap.container import WorkflowContainer, build_workflow_container
from copilot.config import Settings
from copilot.persistence.identifiers import SequentialIdentifierFactory
from copilot.services.workflows.ports import IdentifierFactory
from copilot.tools.mock_supplier_quality import MockBehavior

FIXED_NOW = datetime(2026, 7, 22, 8, 0, tzinfo=UTC)


def fixed_clock() -> datetime:
    """Return one stable UTC instant for deterministic content assertions."""
    return FIXED_NOW


def build_test_container(
    artifact_dir: Path,
    *,
    database_url: str = "sqlite:///unused-test.db",
    use_real_database: bool | None = None,
    knowledge_behavior: MockBehavior | None = None,
    database_behavior: MockBehavior | None = None,
    analytics_behavior: MockBehavior | None = None,
    report_behavior: MockBehavior | None = None,
    interrupt_after: tuple[str, ...] = (),
    ids: IdentifierFactory | None = None,
) -> WorkflowContainer:
    """Compose the real runner/runtime with offline adapters and no real waiting."""
    settings = Settings(
        database_url=database_url,
        artifact_dir=artifact_dir,
        checkpoint_database_path=(
            artifact_dir.parent / f".{artifact_dir.name}-workflow-checkpoints.db"
        ),
        workflow_max_retries=2,
        workflow_retry_delay_seconds=0,
    )
    return build_workflow_container(
        settings,
        ids=ids or SequentialIdentifierFactory(),
        clock=fixed_clock,
        sleeper=lambda _seconds: None,
        knowledge_behavior=knowledge_behavior,
        database_behavior=database_behavior,
        analytics_behavior=analytics_behavior,
        report_behavior=report_behavior,
        use_real_database=use_real_database,
        interrupt_after=interrupt_after,
    )
