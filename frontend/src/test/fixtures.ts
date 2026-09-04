import type {
  ApprovalDetail,
  Artifact,
  Evidence,
  Step,
  Task,
  TaskCreateResponse,
} from "../api/types";

export const task: Task = {
  task_id: "T-TEST-001",
  trace_id: "TRACE-TEST-001",
  status: "COMPLETED",
  runtime_status: "FINISHED",
  task_type: "supplier_quality_analysis.v1",
  created_at: "2026-08-13T08:00:00Z",
  started_at: "2026-08-13T08:00:01Z",
  completed_at: "2026-08-13T08:00:05Z",
  cancelled_at: null,
  current_step: null,
  task_summary:
    "Analyze supplier quality for Q2 2026 and generate a PDF report.",
  pending_approval_id: null,
  pending_clarification: null,
  step_count: 2,
  evidence_count: 3,
  artifact_count: 2,
  error_summary: null,
  interaction_projection: {
    schema_version: "task-interaction-projection.v1",
    initial_user_message: {
      display_text:
        "Analyze supplier quality for Q2 2026 and generate a PDF report.",
      created_at: "2026-08-13T08:00:00Z",
    },
    clarification_rounds: [],
    phase_events: [
      { phase: "UNDERSTANDING", occurred_at: "2026-08-13T08:00:01Z" },
      { phase: "COMPLETED", occurred_at: "2026-08-13T08:00:05Z" },
    ],
    approval_summaries: [],
    result: {
      final_status: "COMPLETED",
      safe_summary:
        "The supplier quality analysis completed with verified evidence.",
    },
  },
};

export const waitingTask: Task = {
  ...task,
  status: "WAITING_APPROVAL",
  runtime_status: "SUSPENDED",
  completed_at: null,
  current_step: "S-DB-01",
  pending_approval_id: "AP-TEST-001",
  artifact_count: 0,
  interaction_projection: {
    ...task.interaction_projection,
    phase_events: [
      { phase: "UNDERSTANDING", occurred_at: "2026-08-13T08:00:01Z" },
      { phase: "WAITING_APPROVAL", occurred_at: "2026-08-13T08:00:02Z" },
    ],
    approval_summaries: [
      {
        approval_id: "AP-TEST-001",
        status: "PENDING",
        safe_label: "Database query requires approval.",
        resolution_action: null,
        created_at: "2026-08-13T08:00:02Z",
        resolved_at: null,
      },
    ],
    result: null,
  },
};

export const clarificationTask: Task = {
  ...task,
  status: "WAITING_CLARIFICATION",
  runtime_status: "SUSPENDED",
  completed_at: null,
  task_type: "accounts_payable_analysis.v1",
  pending_clarification: {
    clarification_id: "CLAR-TEST-001",
    round: 1,
    created_at: "2026-08-13T08:00:02Z",
    questions: [
      {
        field: "time_range",
        reason: "An explicit Accounts Payable invoice date range is required.",
        prompt: "What exact start and end dates should be analyzed?",
        input_type: "date_range",
        required: true,
        allowed_values: [],
        constraints: {},
      },
      {
        field: "legal_entity_ids",
        reason: "The caller has more than one authorized legal entity.",
        prompt: "Select an authorized legal entity.",
        input_type: "single_select",
        required: true,
        allowed_values: ["LE-CN-01", "LE-DE-01"],
        constraints: {},
      },
    ],
  },
  step_count: 0,
  evidence_count: 0,
  artifact_count: 0,
  interaction_projection: {
    ...task.interaction_projection,
    phase_events: [
      { phase: "UNDERSTANDING", occurred_at: "2026-08-13T08:00:01Z" },
      { phase: "WAITING_CLARIFICATION", occurred_at: "2026-08-13T08:00:02Z" },
    ],
    clarification_rounds: [
      {
        clarification_id: "CLAR-TEST-001",
        round: 1,
        status: "PENDING",
        questions: [
          {
            field: "time_range",
            reason:
              "An explicit Accounts Payable invoice date range is required.",
            prompt: "What exact start and end dates should be analyzed?",
            input_type: "date_range",
            required: true,
            allowed_values: [],
            constraints: {},
          },
          {
            field: "legal_entity_ids",
            reason: "The caller has more than one authorized legal entity.",
            prompt: "Select an authorized legal entity.",
            input_type: "single_select",
            required: true,
            allowed_values: ["LE-CN-01", "LE-DE-01"],
            constraints: {},
          },
        ],
        response_display_text: null,
        created_at: "2026-08-13T08:00:02Z",
        submitted_at: null,
        resolved_at: null,
      },
    ],
    approval_summaries: [],
    result: null,
  },
};

export const accountsPayableTask: Task = {
  ...task,
  task_id: "T-AP-TEST-001",
  task_type: "accounts_payable_analysis.v1",
  task_summary:
    "Analyze Accounts Payable exceptions from 2026-04-01 to 2026-06-30.",
  step_count: 14,
  artifact_count: 1,
  interaction_projection: {
    ...task.interaction_projection,
    initial_user_message: {
      display_text:
        "Analyze Accounts Payable exceptions from 2026-04-01 to 2026-06-30.",
      created_at: "2026-08-13T08:00:00Z",
    },
  },
};

export const createdTask: TaskCreateResponse = {
  task_id: task.task_id,
  trace_id: task.trace_id,
  task_status: "CREATED",
  runtime_status: "READY",
  accepted_at: task.created_at,
  status_url: `/v1/tasks/${task.task_id}`,
  artifacts_url: `/v1/tasks/${task.task_id}/artifacts`,
};

export const steps: Step[] = [
  {
    step_id: "S-KB-01",
    tool_name: "knowledge_search",
    purpose: "Retrieve approved supplier-quality policy evidence.",
    status: "SUCCESS",
    depends_on: [],
    attempt_count: 1,
    retry_count: 0,
    started_at: "2026-08-13T08:00:01Z",
    completed_at: "2026-08-13T08:00:02Z",
    latency_ms: 1_000,
    evidence_ids: ["EV-DOC-01"],
    error_code: null,
    error_message: null,
  },
  {
    step_id: "S-DB-01",
    tool_name: "database_query",
    purpose: "Query approved supplier-quality data using a read-only template.",
    status: "TECHNICAL_FAILURE",
    depends_on: ["S-KB-01"],
    attempt_count: 2,
    retry_count: 1,
    started_at: "2026-08-13T08:00:02Z",
    completed_at: "2026-08-13T08:00:04Z",
    latency_ms: 2_000,
    evidence_ids: [],
    error_code: "DATABASE_UNAVAILABLE",
    error_message: "The approved database dependency is unavailable.",
  },
];

export const evidence: Evidence[] = [
  {
    evidence_id: "EV-DOC-01",
    type: "DOCUMENT",
    source: "supplier-quality-policy-v1",
    produced_by: "knowledge_search",
    step_id: "S-KB-01",
    lineage: [],
    confidence: 0.98,
    created_at: "2026-08-13T08:00:02Z",
    query_id: null,
    document_source: "Supplier Quality Manual",
    formula: null,
    input_evidence_ids: [],
    content_summary: "Approved policy defines the supplier defect-rate metric.",
  },
  {
    evidence_id: "EV-DB-01",
    type: "DATABASE",
    source: "supplier_quality_summary_v1",
    produced_by: "database_query",
    step_id: "S-DB-01",
    lineage: ["EV-DOC-01"],
    confidence: null,
    created_at: "2026-08-13T08:00:03Z",
    query_id: "sha256:query-fingerprint",
    document_source: null,
    formula: null,
    input_evidence_ids: [],
    content_summary: "Six approved aggregate rows were returned.",
  },
  {
    evidence_id: "EV-CALC-01",
    type: "CALCULATION",
    source: "quality_metrics.v1",
    produced_by: "analysis_engine",
    step_id: "S-AN-01",
    lineage: ["EV-DB-01"],
    confidence: null,
    created_at: "2026-08-13T08:00:04Z",
    query_id: null,
    document_source: null,
    formula: "defect_count / inspected_count",
    input_evidence_ids: ["EV-DB-01"],
    content_summary: "Deterministic defect-rate calculation completed.",
  },
];

export const artifacts: Artifact[] = [
  {
    artifact_id: "A-PDF-01",
    task_id: task.task_id,
    format: "PDF",
    filename: "supplier-quality-report.pdf",
    media_type: "application/pdf",
    checksum: "sha256:pdf-checksum",
    size_bytes: 12_400,
    created_at: "2026-08-13T08:00:05Z",
  },
  {
    artifact_id: "A-JSON-01",
    task_id: task.task_id,
    format: "JSON",
    filename: "supplier-quality-report.json",
    media_type: "application/json",
    checksum: "sha256:json-checksum",
    size_bytes: 2_400,
    created_at: "2026-08-13T08:00:05Z",
  },
];

export const accountsPayableArtifacts: Artifact[] = [
  {
    artifact_id: "A-AP-JSON-01",
    task_id: accountsPayableTask.task_id,
    format: "JSON",
    filename: "accounts-payable-report.json",
    media_type: "application/json",
    checksum: "sha256:ap-json-checksum",
    size_bytes: 4_096,
    created_at: "2026-08-23T08:00:05Z",
  },
];

export const approval: ApprovalDetail = {
  approval_id: "AP-TEST-001",
  task_id: waitingTask.task_id,
  status: "PENDING",
  step_id: "S-DB-01",
  planning_version: 1,
  tool_name: "database_query",
  tool_version: "1.1",
  editable_fields: ["row_limit"],
  proposed_arguments: {
    query_template_id: "supplier_quality_summary_v1",
    parameters: {
      tenant_id: "TENANT-DEMO",
      start_date: "2026-04-01",
      end_date: "2026-06-30",
      supplier_ids: [],
    },
    schema_version: "quality.v1",
    snapshot_at: "2026-08-13T08:00:00Z",
    row_limit: 10_000,
  },
  resolved_arguments: null,
  reason: "A governed database action requires human approval.",
  resolution_action: null,
  resolution_reason: null,
  created_at: "2026-08-13T08:00:00Z",
  expires_at: "2099-08-13T08:00:00Z",
  resolved_at: null,
  resolved_by: null,
};
