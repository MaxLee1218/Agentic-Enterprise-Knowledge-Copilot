import { chromium } from "@playwright/test";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";

const baseURL = process.env.README_CAPTURE_BASE_URL ?? "http://127.0.0.1:4173";
const outputDir = resolve(
  process.cwd(),
  process.env.README_CAPTURE_OUTPUT ?? "../docs/assets/readme",
);
const taskId = "T-PORTFOLIO-DEMO-001";
const traceId = "TRACE-PORTFOLIO-DEMO-001";
const approvalId = "AP-PORTFOLIO-DEMO-001";
const taskText =
  "Analyze supplier quality deviations for Q2 2026 and generate a PDF management report.";

const completedTask = {
  task_id: taskId,
  trace_id: traceId,
  status: "COMPLETED",
  runtime_status: "FINISHED",
  task_type: "supplier_quality_analysis.v1",
  created_at: "2026-09-01T08:00:00Z",
  started_at: "2026-09-01T08:00:01Z",
  completed_at: "2026-09-01T08:00:08Z",
  cancelled_at: null,
  current_step: null,
  task_summary: taskText,
  pending_approval_id: null,
  pending_clarification: null,
  step_count: 4,
  evidence_count: 3,
  artifact_count: 1,
  error_summary: null,
  interaction_projection: {
    schema_version: "task-interaction-projection.v1",
    initial_user_message: {
      display_text: taskText,
      created_at: "2026-09-01T08:00:00Z",
    },
    clarification_rounds: [],
    phase_events: [
      { phase: "UNDERSTANDING", occurred_at: "2026-09-01T08:00:01Z" },
      { phase: "PLANNING", occurred_at: "2026-09-01T08:00:02Z" },
      { phase: "EXECUTING", occurred_at: "2026-09-01T08:00:03Z" },
      { phase: "VERIFYING", occurred_at: "2026-09-01T08:00:07Z" },
      { phase: "COMPLETED", occurred_at: "2026-09-01T08:00:08Z" },
    ],
    approval_summaries: [],
    result: {
      final_status: "COMPLETED",
      safe_summary:
        "The evidence-backed supplier quality analysis is complete. Verification passed for evidence coverage, numeric consistency, citations, and artifact integrity.",
    },
  },
};

const waitingApprovalTask = {
  ...completedTask,
  status: "WAITING_APPROVAL",
  runtime_status: "SUSPENDED",
  completed_at: null,
  pending_approval_id: approvalId,
  artifact_count: 0,
  interaction_projection: {
    ...completedTask.interaction_projection,
    phase_events: [
      { phase: "UNDERSTANDING", occurred_at: "2026-09-01T08:00:01Z" },
      { phase: "PLANNING", occurred_at: "2026-09-01T08:00:02Z" },
      { phase: "WAITING_APPROVAL", occurred_at: "2026-09-01T08:00:03Z" },
    ],
    approval_summaries: [
      {
        approval_id: approvalId,
        status: "PENDING",
        safe_label: "Controlled database access requires approval.",
        resolution_action: null,
        created_at: "2026-09-01T08:00:03Z",
        resolved_at: null,
      },
    ],
    result: null,
  },
};

const history = {
  items: [
    {
      task_id: taskId,
      task_summary: taskText,
      status: "COMPLETED",
      runtime_status: "FINISHED",
      task_type: "supplier_quality_analysis.v1",
      created_at: "2026-09-01T08:00:00Z",
    },
    {
      task_id: "T-PORTFOLIO-DEMO-002",
      task_summary:
        "Review Accounts Payable compliance for Q2 2026 and identify supported exceptions.",
      status: "COMPLETED",
      runtime_status: "FINISHED",
      task_type: "accounts_payable_analysis.v1",
      created_at: "2026-08-31T09:00:00Z",
    },
  ],
  total: 2,
  limit: 40,
  offset: 0,
};

const artifacts = {
  task_id: taskId,
  artifacts: [
    {
      artifact_id: "A-PORTFOLIO-DEMO-001",
      task_id: taskId,
      format: "PDF",
      filename: "supplier-quality-q2-2026.pdf",
      media_type: "application/pdf",
      checksum: "sha256:12d6c4e8f92b6f37",
      size_bytes: 184320,
      created_at: "2026-09-01T08:00:08Z",
    },
  ],
};

const evidence = {
  task_id: taskId,
  evidence: [
    {
      evidence_id: "EV-DOC-001",
      type: "DOCUMENT",
      source: "supplier-quality-policy-v1.3",
      produced_by: "knowledge_search",
      step_id: "S-1",
      lineage: [],
      confidence: 0.98,
      created_at: "2026-09-01T08:00:02Z",
      query_id: null,
      document_source: "Supplier Quality Manual",
      formula: null,
      input_evidence_ids: [],
      content_summary: "Approved quality policy and reporting requirements.",
    },
    {
      evidence_id: "EV-DB-001",
      type: "DATABASE",
      source: "supplier_quality_summary_v1",
      produced_by: "database_query",
      step_id: "S-2",
      lineage: [],
      confidence: 1,
      created_at: "2026-09-01T08:00:04Z",
      query_id: "sha256:7a93b29c",
      document_source: null,
      formula: null,
      input_evidence_ids: [],
      content_summary:
        "Authorized Q2 2026 supplier inspection and defect dataset.",
    },
    {
      evidence_id: "EV-CALC-001",
      type: "CALCULATION",
      source: "quality_metrics.v1",
      produced_by: "analysis_engine",
      step_id: "S-3",
      lineage: ["EV-DB-001"],
      confidence: 1,
      created_at: "2026-09-01T08:00:05Z",
      query_id: null,
      document_source: null,
      formula: "defect_rate = defect_count / inspected_count",
      input_evidence_ids: ["EV-DB-001"],
      content_summary:
        "Deterministic defect-rate and period-trend calculations.",
    },
  ],
};

const steps = {
  task_id: taskId,
  steps: [
    [
      "S-1",
      "knowledge_search",
      "Retrieve approved supplier-quality policy evidence.",
    ],
    [
      "S-2",
      "database_query",
      "Query the authorized Q2 2026 supplier-quality dataset.",
    ],
    [
      "S-3",
      "analysis_engine",
      "Calculate deterministic quality metrics and trends.",
    ],
    [
      "S-4",
      "report_generator",
      "Generate and verify the management-ready PDF report.",
    ],
  ].map(([step_id, tool_name, purpose], index) => ({
    step_id,
    tool_name,
    purpose,
    status: "SUCCESS",
    depends_on: index === 0 ? [] : [`S-${index}`],
    attempt_count: 1,
    retry_count: 0,
    started_at: `2026-09-01T08:00:0${index + 1}Z`,
    completed_at: `2026-09-01T08:00:0${index + 2}Z`,
    latency_ms: 820 + index * 210,
    evidence_ids: index < 3 ? [evidence.evidence[index].evidence_id] : [],
    error_code: null,
    error_message: null,
  })),
};

function json(route, body, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

await mkdir(outputDir, { recursive: true });
const videoTempDir = await mkdtemp(resolve(tmpdir(), "copilot-readme-video-"));
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
  colorScheme: "light",
  recordVideo: { dir: videoTempDir, size: { width: 1440, height: 900 } },
});
const page = await context.newPage();
const productTour = page.video();
let taskMode = "completed";

await page.route("**/api/v1/**", (route) => {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname;
  if (path === "/api/v1/tasks" && request.method() === "GET")
    return json(route, history);
  if (path === `/api/v1/tasks/${taskId}`)
    return json(
      route,
      taskMode === "approval" ? waitingApprovalTask : completedTask,
    );
  if (path === `/api/v1/tasks/${taskId}/artifacts`)
    return json(
      route,
      taskMode === "approval" ? { task_id: taskId, artifacts: [] } : artifacts,
    );
  if (path === `/api/v1/tasks/${taskId}/evidence`) return json(route, evidence);
  if (path === `/api/v1/tasks/${taskId}/steps`) return json(route, steps);
  if (path === `/api/v1/tasks/${taskId}/approvals/${approvalId}`)
    return json(route, {
      approval_id: approvalId,
      task_id: taskId,
      status: "PENDING",
      step_id: "S-2",
      planning_version: 1,
      tool_name: "database_query",
      tool_version: "1.1",
      editable_fields: ["row_limit"],
      proposed_arguments: { row_limit: 100 },
      resolved_arguments: null,
      reason: "A governed database action requires human approval.",
      resolution_action: null,
      resolution_reason: null,
      created_at: "2026-09-01T08:00:03Z",
      expires_at: "2099-09-01T08:00:03Z",
      resolved_at: null,
      resolved_by: null,
    });
  return json(
    route,
    { code: "NOT_FOUND", message: "Fixture route not found" },
    404,
  );
});

async function settle() {
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
}

await page.goto(baseURL);
await settle();
await page.waitForTimeout(1400);
await page.screenshot({
  path: resolve(outputDir, "product-workspace.png"),
  fullPage: true,
});

await page.goto(`${baseURL}/tasks/${taskId}`);
await settle();
await page.waitForTimeout(1800);
await page.screenshot({
  path: resolve(outputDir, "verified-task.png"),
  fullPage: true,
});

await page.getByRole("button", { name: "Evidence", exact: true }).click();
await page.getByRole("dialog", { name: "Evidence" }).waitFor();
await page.waitForTimeout(1800);
await page.screenshot({
  path: resolve(outputDir, "evidence-lineage.png"),
  fullPage: true,
});

taskMode = "approval";
await page.goto(`${baseURL}/tasks/${taskId}`);
await settle();
await page
  .getByRole("heading", { name: "Review a controlled action" })
  .waitFor();
await page.waitForTimeout(2200);
await page.screenshot({
  path: resolve(outputDir, "approval-workflow.png"),
  fullPage: true,
});

await page.close();
if (productTour) {
  await productTour.saveAs(resolve(outputDir, "product-tour.webm"));
}
await context.close();
await browser.close();
await rm(videoTempDir, { recursive: true, force: true });
console.log(`README screenshots written to ${outputDir}`);
