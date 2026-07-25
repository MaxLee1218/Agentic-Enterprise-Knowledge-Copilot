"""Dependency composition for the deterministic Supplier Quality workflow."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from time import sleep

from copilot.bootstrap.knowledge import build_http_knowledge_client
from copilot.config import PROJECT_ROOT, Settings
from copilot.contracts.validators import utc_now
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.persistence.artifact_repository import LocalArtifactRepository
from copilot.persistence.audit_repository import (
    InMemoryToolAuditRepository,
    InMemoryWorkflowAuditRepository,
)
from copilot.persistence.identifiers import UuidIdentifierFactory
from copilot.persistence.task_repository import InMemoryWorkflowRepository
from copilot.policies.offline import OfflineSupplierQualityAuthorizer
from copilot.services.workflows.dependency import DependencyChecker
from copilot.services.workflows.fixed_plan import SupplierQualityAnalysisPlanFactory
from copilot.services.workflows.inputs import StepInputBuilder
from copilot.services.workflows.ports import IdentifierFactory
from copilot.services.workflows.retry import WorkflowRetryPolicy
from copilot.services.workflows.runner import WorkflowRunner
from copilot.services.workflows.service import SupplierQualityWorkflowService
from copilot.services.workflows.state_machine import TaskStateMachine
from copilot.services.workflows.validation import PlanValidator
from copilot.services.workflows.verification import WorkflowVerifier
from copilot.tools.database import DatabaseConnection, DatabaseTool
from copilot.tools.executor import ToolExecutor
from copilot.tools.knowledge import HttpKnowledgeClient, KnowledgeTool
from copilot.tools.mock_supplier_quality import (
    MockAnalyticsTool,
    MockBehavior,
    MockDatabaseTool,
    MockKnowledgeTool,
    MockReportTool,
)
from copilot.tools.registry import ToolRegistry


@dataclass(slots=True)
class WorkflowContainer:
    """Owned runtime resources plus inspectable local adapters."""

    service: SupplierQualityWorkflowService
    executor: ToolExecutor
    registry: ToolRegistry
    evidence: InMemoryEvidenceLedger
    artifacts: LocalArtifactRepository
    repository: InMemoryWorkflowRepository
    tool_audit: InMemoryToolAuditRepository
    workflow_audit: InMemoryWorkflowAuditRepository
    knowledge_tool: MockKnowledgeTool | KnowledgeTool
    knowledge_client: HttpKnowledgeClient | None
    database_tool: MockDatabaseTool | DatabaseTool
    analytics_tool: MockAnalyticsTool
    report_tool: MockReportTool

    def close(self) -> None:
        """Release the executor's owned worker pool."""
        self.executor.close()
        if self.knowledge_client is not None:
            self.knowledge_client.close()
        if isinstance(self.database_tool, DatabaseTool):
            self.database_tool.close()

    def __enter__(self) -> WorkflowContainer:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def build_workflow_container(
    settings: Settings,
    *,
    ids: IdentifierFactory | None = None,
    clock: Callable[[], datetime] = utc_now,
    sleeper: Callable[[float], None] | None = None,
    knowledge_behavior: MockBehavior | None = None,
    database_behavior: MockBehavior | None = None,
    analytics_behavior: MockBehavior | None = None,
    report_behavior: MockBehavior | None = None,
    use_real_database: bool | None = None,
) -> WorkflowContainer:
    """Construct all application ports and offline adapters without global mutable state."""
    identifier_factory = ids or UuidIdentifierFactory()
    evidence = InMemoryEvidenceLedger(
        id_factory=lambda: identifier_factory.new_id("E"),
        clock=clock,
    )
    artifacts = LocalArtifactRepository(settings.artifact_path, clock=clock)
    repository = InMemoryWorkflowRepository()
    tool_audit = InMemoryToolAuditRepository()
    workflow_audit = InMemoryWorkflowAuditRepository()
    knowledge_client: HttpKnowledgeClient | None = None
    if settings.app_env == "production":
        knowledge_client = build_http_knowledge_client(
            settings,
            timeout_seconds=min(settings.rag_timeout_seconds, 10.0),
        )
        knowledge_tool: MockKnowledgeTool | KnowledgeTool = KnowledgeTool(knowledge_client)
    else:
        knowledge_tool = MockKnowledgeTool(knowledge_behavior)
    real_database_enabled = (
        settings.app_env == "production" if use_real_database is None else use_real_database
    )
    if real_database_enabled:
        if database_behavior is not None:
            raise ValueError("database_behavior cannot be used with the real Database Tool")
        database_tool: MockDatabaseTool | DatabaseTool = DatabaseTool(
            DatabaseConnection(
                settings.database_url,
                read_only=True,
                base_directory=PROJECT_ROOT,
            ),
            statement_timeout_seconds=settings.database_statement_timeout_seconds,
        )
    else:
        database_tool = MockDatabaseTool(database_behavior)
    analytics_tool = MockAnalyticsTool(analytics_behavior)
    report_tool = MockReportTool(
        evidence_reader=evidence,
        artifact_store=artifacts,
        ids=identifier_factory,
        clock=clock,
        behavior=report_behavior,
    )
    registry = ToolRegistry()
    for tool in (knowledge_tool, database_tool, analytics_tool, report_tool):
        registry.register(tool)
    executor = ToolExecutor(
        registry=registry,
        authorizer=OfflineSupplierQualityAuthorizer(),
        evidence_recorder=evidence,
        audit_sink=tool_audit,
        clock=clock,
    )
    plan_factory = SupplierQualityAnalysisPlanFactory(registry)
    state_machine = TaskStateMachine(clock=clock, ids=identifier_factory)
    runner = WorkflowRunner(
        tool_executor=executor,
        registry=registry,
        plan_validator=PlanValidator(
            registry=registry,
            max_task_steps=settings.max_task_steps,
        ),
        dependency_checker=DependencyChecker(),
        input_builder=StepInputBuilder(),
        retry_policy=WorkflowRetryPolicy(
            max_retries=settings.workflow_max_retries,
            retry_delay_seconds=settings.workflow_retry_delay_seconds,
        ),
        verifier=WorkflowVerifier(artifacts),
        evidence_reader=evidence,
        artifact_store=artifacts,
        repository=repository,
        audit_sink=workflow_audit,
        state_machine=state_machine,
        ids=identifier_factory,
        clock=clock,
        sleeper=sleeper or sleep,
    )
    service = SupplierQualityWorkflowService(
        runner=runner,
        plan_factory=plan_factory,
        ids=identifier_factory,
        clock=clock,
    )
    return WorkflowContainer(
        service=service,
        executor=executor,
        registry=registry,
        evidence=evidence,
        artifacts=artifacts,
        repository=repository,
        tool_audit=tool_audit,
        workflow_audit=workflow_audit,
        knowledge_tool=knowledge_tool,
        knowledge_client=knowledge_client,
        database_tool=database_tool,
        analytics_tool=analytics_tool,
        report_tool=report_tool,
    )
