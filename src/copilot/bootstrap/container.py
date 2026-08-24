"""Dependency composition for the shared governed domain workflow."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from time import monotonic, sleep
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy.engine import make_url

from copilot.agent.graph import LangGraphWorkflowEngine
from copilot.agent.runtime import GraphNodeRuntime
from copilot.agent.state import checkpoint_serializer
from copilot.bootstrap.knowledge import build_http_knowledge_client
from copilot.config import PROJECT_ROOT, Settings
from copilot.contracts import ACCOUNTS_PAYABLE_CONTRACT_PROFILES, CapabilityName
from copilot.contracts.validators import utc_now
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.evidence.workflow import WorkflowVerifier
from copilot.llm.deepseek import DeepSeekProvider
from copilot.llm.manifest import PlannerToolManifestBuilder
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.llm.planning import LLMPlanningService
from copilot.observability import (
    InMemoryObservability,
    InMemoryTracer,
    MetricsRegistry,
    ObservabilityContextManager,
    PerformanceAnalyzer,
    PerformanceLimits,
    StructuredEventLogger,
    configure_logging,
)
from copilot.persistence.approval_repository import ApprovalRepository
from copilot.persistence.artifact_repository import LocalArtifactRepository
from copilot.persistence.audit_repository import ToolAuditRepository, WorkflowAuditRepository
from copilot.persistence.checkpoint import require_postgres_checkpoint_schema
from copilot.persistence.database import PersistenceDatabase
from copilot.persistence.identifiers import UuidIdentifierFactory
from copilot.persistence.task_repository import WorkflowRepository
from copilot.policies.approval import SupplierQualityApprovalPolicy
from copilot.policies.data_access import DataAccessPolicy
from copilot.policies.mcp_access import MCPAccessPolicy, MCPAwareToolAuthorizer
from copilot.policies.offline import OfflineSupplierQualityAuthorizer
from copilot.policies.permissions import PermissionMatrix
from copilot.security import OutputGuard, PromptInjectionDetector, SensitiveDataRegistry
from copilot.services.approval_service import ApprovalGateService, ApprovalService
from copilot.services.artifact_service import ArtifactService
from copilot.services.domains import builtin_domain_manifest_registry
from copilot.services.health import ReadinessService
from copilot.services.llm import LLMGenerationOptions, LLMProvider
from copilot.services.task_intake import IntakeLimits
from copilot.services.task_service import NaturalLanguageTaskService
from copilot.services.workflows.dependency import DependencyChecker
from copilot.services.workflows.fixed_plan import SupplierQualityAnalysisPlanFactory
from copilot.services.workflows.inputs import StepInputBuilder
from copilot.services.workflows.ports import IdentifierFactory
from copilot.services.workflows.retry import WorkflowRetryPolicy
from copilot.services.workflows.service import SupplierQualityWorkflowService
from copilot.services.workflows.state_machine import TaskStateMachine
from copilot.services.workflows.validation import PlanValidator
from copilot.tools.analytics import AccountsPayableAnalyticsTool, AnalyticsTool
from copilot.tools.cancellation import InvocationCancellationRegistry
from copilot.tools.database import DatabaseConnection, DatabaseTool
from copilot.tools.database.schema_registry import SchemaRegistry
from copilot.tools.executor import ToolExecutor
from copilot.tools.knowledge import (
    AccountsPayablePolicyTool,
    HttpKnowledgeClient,
    KnowledgeTool,
    load_ap_policy_bundle,
)
from copilot.tools.mock_supplier_quality import (
    MockAnalyticsTool,
    MockBehavior,
    MockDatabaseTool,
    MockKnowledgeTool,
    MockReportTool,
)
from copilot.tools.registry import ToolCancellationMode, ToolRegistry
from copilot.tools.reporting import AccountsPayableReportTool, ReportTool


@dataclass(slots=True)
class WorkflowContainer:
    """Owned runtime resources plus inspectable local adapters."""

    service: SupplierQualityWorkflowService
    task_service: NaturalLanguageTaskService
    executor: ToolExecutor
    registry: ToolRegistry
    evidence: InMemoryEvidenceLedger
    artifacts: LocalArtifactRepository
    repository: WorkflowRepository
    tool_audit: ToolAuditRepository
    workflow_audit: WorkflowAuditRepository
    approval_repository: ApprovalRepository
    approval_service: ApprovalService
    artifact_service: ArtifactService
    knowledge_tool: MockKnowledgeTool | KnowledgeTool
    knowledge_client: HttpKnowledgeClient | None
    database_tool: MockDatabaseTool | DatabaseTool
    analytics_tool: MockAnalyticsTool | AnalyticsTool
    report_tool: MockReportTool | ReportTool
    ap_knowledge_tool: AccountsPayablePolicyTool
    ap_database_tool: DatabaseTool
    ap_analytics_tool: AccountsPayableAnalyticsTool
    ap_report_tool: AccountsPayableReportTool
    graph_runtime: GraphNodeRuntime
    engine: LangGraphWorkflowEngine
    planning_service: LLMPlanningService | None = None
    owned_llm_provider: DeepSeekProvider | None = None
    checkpoint_connection: Any | None = None
    persistence_database: PersistenceDatabase | None = None
    observability: InMemoryObservability | None = None
    readiness: ReadinessService | None = None
    cancellations: InvocationCancellationRegistry | None = None

    def close(self) -> None:
        """Release the executor's owned worker pool."""
        self.executor.close()
        if self.knowledge_client is not None:
            self.knowledge_client.close()
        if self.owned_llm_provider is not None:
            self.owned_llm_provider.close()
        if isinstance(self.database_tool, DatabaseTool):
            self.database_tool.close()
        self.ap_database_tool.close()
        self.evidence.close()
        self.artifacts.close()
        self.repository.close()
        self.tool_audit.close()
        self.workflow_audit.close()
        self.approval_repository.close()
        if self.checkpoint_connection is not None:
            close_checkpoint = getattr(self.checkpoint_connection, "close", None)
            if callable(close_checkpoint):
                close_checkpoint()
        if self.persistence_database is not None:
            self.persistence_database.dispose()

    def __enter__(self) -> WorkflowContainer:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def build_workflow_container(
    settings: Settings,
    *,
    ids: IdentifierFactory | None = None,
    clock: Callable[[], datetime] = utc_now,
    timer: Callable[[], float] = monotonic,
    sleeper: Callable[[float], None] | None = None,
    knowledge_behavior: MockBehavior | None = None,
    database_behavior: MockBehavior | None = None,
    analytics_behavior: MockBehavior | None = None,
    report_behavior: MockBehavior | None = None,
    use_real_database: bool | None = None,
    interrupt_after: tuple[str, ...] = (),
    llm_provider: LLMProvider | None = None,
    mcp_access_policy: MCPAccessPolicy | None = None,
) -> WorkflowContainer:
    """Construct all application ports and offline adapters without global mutable state."""
    observability_context = ObservabilityContextManager()
    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        context=observability_context,
        max_summary_length=settings.max_log_summary_length,
    )
    performance_limits = PerformanceLimits(
        max_task_duration_seconds=settings.max_total_execution_seconds,
        max_step_duration_seconds=settings.max_step_duration_seconds,
        max_database_rows=settings.max_database_rows,
        max_evidence_items=settings.max_evidence_items,
        max_report_size_bytes=settings.report_max_size_bytes,
        max_llm_output_tokens=settings.llm_max_output_tokens,
    )
    metrics = MetricsRegistry(window_size=settings.metrics_window_size, clock=clock)
    tracer = InMemoryTracer(
        context=observability_context,
        max_spans=settings.max_trace_spans,
        max_attributes=settings.max_trace_attributes,
        max_attribute_length=settings.max_trace_attribute_length,
        clock=clock,
        timer=timer,
    )
    observability = InMemoryObservability(
        context=observability_context,
        tracer=tracer,
        metrics=metrics,
        analyzer=PerformanceAnalyzer(performance_limits),
        logger=StructuredEventLogger(max_summary_length=settings.max_log_summary_length),
        max_step_duration_seconds=settings.max_step_duration_seconds,
        enabled=settings.observability_enabled,
        trace_enabled=settings.trace_enabled,
        metrics_enabled=settings.metrics_enabled,
        timer=timer,
    )
    identifier_factory = ids or UuidIdentifierFactory()
    sensitive_registry = SensitiveDataRegistry()
    output_guard = OutputGuard(sensitive_registry)
    injection_detector = PromptInjectionDetector()
    permission_matrix = PermissionMatrix()
    data_access_policy = DataAccessPolicy()
    persistence_database: PersistenceDatabase | None = None
    if settings.checkpoint_enabled or settings.persistence_database_url is not None:
        persistence_database = PersistenceDatabase(
            settings.effective_persistence_database_url,
            base_directory=PROJECT_ROOT,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout_seconds=settings.db_pool_timeout_seconds,
            pool_recycle_seconds=settings.db_pool_recycle_seconds,
        )
        persistence_database.connect_with_retry(
            max_attempts=settings.db_connect_max_attempts,
            retry_delay_seconds=settings.db_connect_retry_delay_seconds,
        )
        if settings.persistence_auto_create_schema:
            persistence_database.create_schema_for_tests()
        else:
            persistence_database.require_schema()
    evidence = InMemoryEvidenceLedger(
        id_factory=lambda: identifier_factory.new_id("E"),
        clock=clock,
        database_path=persistence_database,
        output_guard=output_guard,
        injection_detector=injection_detector,
        max_items_per_task=settings.max_evidence_items,
        initialize_schema=False,
    )
    artifacts = LocalArtifactRepository(
        settings.artifact_path,
        clock=clock,
        max_size_bytes=settings.report_max_size_bytes,
        database_path=persistence_database,
        output_guard=output_guard,
        initialize_schema=False,
    )
    repository = WorkflowRepository(persistence_database, initialize_schema=False)
    tool_audit = ToolAuditRepository(persistence_database, initialize_schema=False)
    workflow_audit = WorkflowAuditRepository(
        persistence_database,
        initialize_schema=False,
    )
    approval_repository = ApprovalRepository(
        persistence_database,
        initialize_schema=False,
    )
    schema_registry = SchemaRegistry()
    knowledge_client: HttpKnowledgeClient | None = None
    if settings.knowledge_provider == "http":
        knowledge_client = build_http_knowledge_client(
            settings,
            timeout_seconds=min(
                settings.rag_timeout_seconds,
                float(KnowledgeTool.definition.timeout.attempt_seconds) - 1.0,
            ),
            max_attempts=1,
        )
        knowledge_tool: MockKnowledgeTool | KnowledgeTool = KnowledgeTool(knowledge_client)
    else:
        knowledge_tool = MockKnowledgeTool(knowledge_behavior)
    real_database_enabled = (
        settings.database_provider == "sqlalchemy"
        if use_real_database is None
        else use_real_database
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
            schema_registry=schema_registry,
            data_access_policy=data_access_policy,
        )
    else:
        database_tool = MockDatabaseTool(database_behavior)
    analytics_tool: MockAnalyticsTool | AnalyticsTool
    if analytics_behavior is None:
        analytics_tool = AnalyticsTool(evidence)
    else:
        analytics_tool = MockAnalyticsTool(analytics_behavior)
    report_tool: MockReportTool | ReportTool
    if report_behavior is None:
        report_tool = ReportTool(
            evidence_reader=evidence,
            artifact_store=artifacts,
            ids=identifier_factory,
            clock=clock,
            output_guard=output_guard,
        )
    else:
        report_tool = MockReportTool(
            evidence_reader=evidence,
            artifact_store=artifacts,
            ids=identifier_factory,
            clock=clock,
            behavior=report_behavior,
        )
    ap_policy_bundle = load_ap_policy_bundle(settings.ap_policy_bundle_dir)
    ap_knowledge_tool = AccountsPayablePolicyTool(ap_policy_bundle)
    ap_database_tool = DatabaseTool.accounts_payable(
        DatabaseConnection(
            settings.database_url,
            read_only=True,
            base_directory=PROJECT_ROOT,
        ),
        statement_timeout_seconds=settings.database_statement_timeout_seconds,
        data_access_policy=data_access_policy,
    )
    ap_analytics_tool = AccountsPayableAnalyticsTool(evidence)
    ap_report_tool = AccountsPayableReportTool(
        evidence_reader=evidence,
        artifact_store=artifacts,
        ids=identifier_factory,
        clock=clock,
        output_guard=output_guard,
    )
    registry = ToolRegistry()
    registry.register(knowledge_tool, cancellation_mode=ToolCancellationMode.NON_CANCELLABLE)
    registry.register(database_tool, cancellation_mode=ToolCancellationMode.NON_CANCELLABLE)
    registry.register(analytics_tool, cancellation_mode=ToolCancellationMode.COOPERATIVE)
    registry.register(report_tool, cancellation_mode=ToolCancellationMode.NON_CANCELLABLE)
    registry.register_profile(
        ap_knowledge_tool,
        contract_profiles=(ACCOUNTS_PAYABLE_CONTRACT_PROFILES[CapabilityName.KNOWLEDGE_SEARCH],),
        cancellation_mode=ToolCancellationMode.NON_CANCELLABLE,
    )
    registry.register_profile(
        ap_database_tool,
        contract_profiles=(ACCOUNTS_PAYABLE_CONTRACT_PROFILES[CapabilityName.DATABASE_QUERY],),
        cancellation_mode=ToolCancellationMode.NON_CANCELLABLE,
    )
    registry.register_profile(
        ap_analytics_tool,
        contract_profiles=(ACCOUNTS_PAYABLE_CONTRACT_PROFILES[CapabilityName.ANALYSIS_ENGINE],),
        cancellation_mode=ToolCancellationMode.COOPERATIVE,
    )
    registry.register_profile(
        ap_report_tool,
        contract_profiles=(ACCOUNTS_PAYABLE_CONTRACT_PROFILES[CapabilityName.REPORT_GENERATOR],),
        cancellation_mode=ToolCancellationMode.NON_CANCELLABLE,
    )
    domain_manifests = builtin_domain_manifest_registry()
    approval_gate = ApprovalGateService(
        repository=approval_repository,
        audit_sink=workflow_audit,
        ids=identifier_factory,
        clock=clock,
        ttl_seconds=settings.approval_ttl_seconds,
    )
    approval_policy = SupplierQualityApprovalPolicy()
    cancellations = InvocationCancellationRegistry()
    local_authorizer = OfflineSupplierQualityAuthorizer(
        approval_repository,
        clock=clock,
        permission_matrix=permission_matrix,
        data_access_policy=data_access_policy,
    )
    authorizer = (
        MCPAwareToolAuthorizer(
            local_authorizer=local_authorizer,
            policy=mcp_access_policy,
            approval_repository=approval_repository,
        )
        if mcp_access_policy is not None
        else local_authorizer
    )
    executor = ToolExecutor(
        registry=registry,
        authorizer=authorizer,
        evidence_recorder=evidence,
        audit_sink=tool_audit,
        clock=clock,
        output_guard=output_guard,
        injection_detector=injection_detector,
        observability=observability,
        timer=timer,
        max_step_duration_seconds=settings.max_step_duration_seconds,
        max_database_rows=settings.max_database_rows,
        cancellation_registry=cancellations,
    )
    plan_factory = SupplierQualityAnalysisPlanFactory(registry)
    plan_validator = PlanValidator(
        registry=registry,
        max_task_steps=settings.max_task_steps,
        max_planning_version=settings.max_replan_count + 1,
        domain_manifests=domain_manifests,
    )
    owned_llm_provider: DeepSeekProvider | None = None
    effective_llm_provider = llm_provider
    if effective_llm_provider is None and settings.llm_provider == "deepseek":
        owned_llm_provider = DeepSeekProvider(
            api_key=settings.require_llm_api_key().get_secret_value(),
            model=settings.llm_model,
            base_url=str(settings.llm_base_url),
            connect_timeout_seconds=settings.llm_connect_timeout_seconds,
            read_timeout_seconds=settings.llm_read_timeout_seconds,
            max_retries=settings.llm_max_retries,
            retry_base_delay_seconds=settings.llm_retry_base_delay_seconds,
            user_agent=settings.llm_user_agent,
            trace_header=settings.llm_trace_header,
        )
        effective_llm_provider = owned_llm_provider
    planning_service = (
        LLMPlanningService(
            provider=effective_llm_provider,
            manifest_builder=PlannerToolManifestBuilder(registry),
            validator=plan_validator,
            options=LLMGenerationOptions(
                temperature=settings.llm_temperature,
                max_output_tokens=settings.llm_max_output_tokens,
            ),
            max_plan_repair_attempts=settings.max_plan_repair_attempts,
            domain_manifests=domain_manifests,
        )
        if effective_llm_provider is not None
        else None
    )
    state_machine = TaskStateMachine(clock=clock, ids=identifier_factory)
    runtime = GraphNodeRuntime(
        tool_executor=executor,
        registry=registry,
        plan_validator=plan_validator,
        dependency_checker=DependencyChecker(),
        input_builder=StepInputBuilder(
            domain_manifests,
            ap_policy_collection_id=ap_policy_bundle.corpus.collection_id,
            ap_policy_rule_manifest=ap_policy_bundle.rule_manifest,
            ap_policy_snapshot_id=ap_knowledge_tool.index_snapshot_id,
            ap_database_row_limit=settings.max_database_rows,
        ),
        retry_policy=WorkflowRetryPolicy(
            max_retries=settings.workflow_max_retries,
            retry_delay_seconds=settings.workflow_retry_delay_seconds,
        ),
        verifier=WorkflowVerifier(
            artifacts,
            evidence,
            allowed_tables=schema_registry.list_tables(),
            allowed_columns=schema_registry.list_columns(),
            sensitive_fields=schema_registry.list_sensitive_columns(),
            allowed_query_templates=schema_registry.list_templates(),
            verifier_profile_id=None,
            clock=clock,
        ),
        evidence_reader=evidence,
        artifact_store=artifacts,
        repository=repository,
        audit_sink=workflow_audit,
        state_machine=state_machine,
        ids=identifier_factory,
        clock=clock,
        sleeper=sleeper or sleep,
        approval_gate=approval_gate,
        approval_repository=approval_repository,
        approval_policy=approval_policy,
        max_task_steps=settings.max_task_steps,
        max_replan_count=settings.max_replan_count,
        max_plan_repair_attempts=settings.max_plan_repair_attempts,
        planning_service=planning_service,
        permission_matrix=permission_matrix,
        observability=observability,
        domain_manifests=domain_manifests,
    )
    checkpoint_connection: Any | None = None
    checkpointer: BaseCheckpointSaver[str]
    checkpoint_serde = checkpoint_serializer()
    if settings.checkpoint_enabled:
        checkpoint_backend = make_url(
            settings.effective_persistence_database_url
        ).get_backend_name()
        if checkpoint_backend == "postgresql":
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg import Connection
            from psycopg.rows import dict_row

            assert persistence_database is not None
            require_postgres_checkpoint_schema(persistence_database.engine)
            checkpoint_url = make_url(settings.effective_persistence_database_url).set(
                drivername="postgresql"
            )
            postgres_connection = Connection.connect(
                checkpoint_url.render_as_string(hide_password=False),
                autocommit=True,
                prepare_threshold=0,
                row_factory=dict_row,
            )
            checkpointer = PostgresSaver(postgres_connection, serde=checkpoint_serde)
            checkpoint_connection = postgres_connection
        else:
            settings.checkpoint_database_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_connection = sqlite3.connect(
                settings.checkpoint_database_path,
                timeout=settings.checkpoint_connection_timeout_seconds,
                check_same_thread=False,
            )
            checkpointer = SqliteSaver(checkpoint_connection, serde=checkpoint_serde)
    else:
        checkpointer = InMemorySaver(serde=checkpoint_serde)
    engine = LangGraphWorkflowEngine(
        runtime=runtime,
        checkpointer=checkpointer,
        repository=repository,
        evidence_reader=evidence,
        state_machine=state_machine,
        ids=identifier_factory,
        clock=clock,
        recursion_limit=settings.graph_recursion_limit,
        max_task_steps=settings.max_task_steps,
        interrupt_after=interrupt_after,
        observability=observability,
        timer=timer,
    )
    approval_service = ApprovalService(
        repository=approval_repository,
        engine=engine,
        registry=registry,
        audit_sink=workflow_audit,
        ids=identifier_factory,
        clock=clock,
        permission_matrix=permission_matrix,
        observability=observability,
    )
    service = SupplierQualityWorkflowService(
        engine=engine,
        plan_factory=plan_factory,
        ids=identifier_factory,
        clock=clock,
        max_total_execution_seconds=settings.max_total_execution_seconds,
    )
    task_service = NaturalLanguageTaskService(
        engine=engine,
        ids=identifier_factory,
        clock=clock,
        limits=IntakeLimits(
            max_task_text_length=settings.max_task_text_length,
            max_metadata_bytes=settings.max_task_metadata_bytes,
            max_metadata_depth=settings.max_task_metadata_depth,
            max_metadata_items=settings.max_task_metadata_items,
            max_task_steps=settings.max_task_steps,
            max_total_execution_seconds=settings.max_total_execution_seconds,
            force_read_only=settings.task_force_read_only,
            require_approval=settings.task_require_approval_by_default,
        ),
        repository=repository,
        evidence=evidence,
        artifacts=artifacts,
        approvals=approval_repository,
        state_machine=state_machine,
        audit_sink=workflow_audit,
        injection_detector=injection_detector,
        output_guard=output_guard,
        permission_matrix=permission_matrix,
        observability=observability,
        cancellations=cancellations,
    )
    artifact_service = ArtifactService(
        repository=artifacts,
        tasks=task_service,
        audit_sink=workflow_audit,
        ids=identifier_factory,
        clock=clock,
        permission_matrix=permission_matrix,
    )
    rag_probe = (
        (lambda: knowledge_client.health_check().healthy) if knowledge_client is not None else None
    )
    task_dependencies = {"database", "artifact_storage"}
    business_database_probe = (
        database_tool.check_ready if isinstance(database_tool, DatabaseTool) else None
    )
    if business_database_probe is not None:
        task_dependencies.add("business_database")
    if rag_probe is not None:
        task_dependencies.add("rag")
    readiness = ReadinessService(
        {
            "database": persistence_database.ping if persistence_database is not None else None,
            "business_database": business_database_probe,
            "artifact_storage": artifacts.check_ready,
            "rag": rag_probe,
        },
        task_dependencies=frozenset(task_dependencies),
    )
    return WorkflowContainer(
        service=service,
        task_service=task_service,
        executor=executor,
        registry=registry,
        evidence=evidence,
        artifacts=artifacts,
        repository=repository,
        tool_audit=tool_audit,
        workflow_audit=workflow_audit,
        approval_repository=approval_repository,
        approval_service=approval_service,
        artifact_service=artifact_service,
        knowledge_tool=knowledge_tool,
        knowledge_client=knowledge_client,
        database_tool=database_tool,
        analytics_tool=analytics_tool,
        report_tool=report_tool,
        ap_knowledge_tool=ap_knowledge_tool,
        ap_database_tool=ap_database_tool,
        ap_analytics_tool=ap_analytics_tool,
        ap_report_tool=ap_report_tool,
        graph_runtime=runtime,
        engine=engine,
        planning_service=planning_service,
        owned_llm_provider=owned_llm_provider,
        checkpoint_connection=checkpoint_connection,
        persistence_database=persistence_database,
        observability=observability,
        readiness=readiness,
        cancellations=cancellations,
    )


def build_application(settings: Settings) -> WorkflowContainer:
    """Build the shared API/CLI application, including an offline mock LLM when configured."""
    provider: LLMProvider | None = OfflineMockLLM() if settings.llm_provider == "mock" else None
    return build_workflow_container(settings, llm_provider=provider)


__all__ = ["WorkflowContainer", "build_application", "build_workflow_container"]
