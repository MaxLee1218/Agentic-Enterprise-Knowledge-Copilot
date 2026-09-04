import { expect, test, type Page } from "@playwright/test";

const taskId = "T-WORKSPACE-E2E-001";
const traceId = "TRACE-WORKSPACE-E2E-001";
const taskText =
  "Analyze supplier quality issues in Q2 2026 and generate a PDF report.";

function completedTask() {
  return {
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
        { phase: "EXECUTING", occurred_at: "2026-09-01T08:00:03Z" },
        { phase: "VERIFYING", occurred_at: "2026-09-01T08:00:07Z" },
        { phase: "COMPLETED", occurred_at: "2026-09-01T08:00:08Z" },
      ],
      approval_summaries: [],
      result: {
        final_status: "COMPLETED",
        safe_summary:
          "The evidence-backed supplier quality analysis is complete.",
      },
    },
  };
}

async function routeHistory(page: Page) {
  await page.route("**/api/v1/tasks?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            task_id: taskId,
            task_summary: taskText,
            status: "COMPLETED",
            runtime_status: "FINISHED",
            task_type: "supplier_quality_analysis.v1",
            created_at: "2026-09-01T08:00:00Z",
          },
        ],
        total: 1,
        limit: 40,
        offset: 0,
      }),
    }),
  );
}

test("new task workspace submits natural language only", async ({ page }) => {
  await routeHistory(page);
  let submitted: unknown;
  let idempotencyKey: string | undefined;
  await page.route("**/api/v1/tasks", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    submitted = route.request().postDataJSON();
    idempotencyKey =
      (await route.request().headerValue("Idempotency-Key")) ?? undefined;
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        task_id: taskId,
        trace_id: traceId,
        task_status: "CREATED",
        runtime_status: "READY",
        accepted_at: "2026-09-01T08:00:00Z",
        status_url: `/v1/tasks/${taskId}`,
        artifacts_url: `/v1/tasks/${taskId}/artifacts`,
      }),
    });
  });
  await page.route(`**/api/v1/tasks/${taskId}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(completedTask()),
    }),
  );
  await page.route(`**/api/v1/tasks/${taskId}/artifacts`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ task_id: taskId, artifacts: [] }),
    }),
  );

  await page.goto("/");
  await expect(
    page.getByRole("heading", {
      name: "Welcome to the Enterprise Knowledge Copilot.",
    }),
  ).toBeVisible();
  await expect(page.getByLabel(/task type/i)).toHaveCount(0);
  const composer = page.getByRole("textbox", {
    name: "Message the Enterprise Knowledge Copilot",
  });
  await composer.fill(taskText);
  await composer.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}$`));
  expect(submitted).toEqual({ task: taskText });
  expect(idempotencyKey).toMatch(/^[0-9a-f-]{36}$/i);
});

test("refresh-safe conversation exposes Artifact and lazy detail drawers", async ({
  page,
}) => {
  await routeHistory(page);
  await page.route(`**/api/v1/tasks/${taskId}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(completedTask()),
    }),
  );
  await page.route(`**/api/v1/tasks/${taskId}/artifacts`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        task_id: taskId,
        artifacts: [
          {
            artifact_id: "A-E2E-001",
            task_id: taskId,
            format: "PDF",
            filename: "supplier-quality-report.pdf",
            media_type: "application/pdf",
            checksum: "sha256:e2e",
            size_bytes: 8192,
            created_at: "2026-09-01T08:00:08Z",
          },
        ],
      }),
    }),
  );
  await page.route(`**/api/v1/tasks/${taskId}/evidence`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        task_id: taskId,
        evidence: [
          {
            evidence_id: "EV-E2E-001",
            type: "DOCUMENT",
            source: "supplier-quality-policy-v1",
            produced_by: "knowledge_search",
            step_id: "S-1",
            lineage: [],
            confidence: 0.98,
            created_at: "2026-09-01T08:00:02Z",
            query_id: null,
            document_source: "Supplier Quality Manual",
            formula: null,
            input_evidence_ids: [],
            content_summary: "Approved supplier-quality policy evidence.",
          },
        ],
      }),
    }),
  );
  await page.route(`**/api/v1/tasks/${taskId}/steps`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        task_id: taskId,
        steps: [
          {
            step_id: "S-1",
            tool_name: "knowledge_search",
            purpose: "Retrieve approved supplier-quality policy evidence.",
            status: "SUCCESS",
            depends_on: [],
            attempt_count: 1,
            retry_count: 0,
            started_at: "2026-09-01T08:00:01Z",
            completed_at: "2026-09-01T08:00:02Z",
            latency_ms: 1000,
            evidence_ids: ["EV-E2E-001"],
            error_code: null,
            error_message: null,
          },
        ],
      }),
    }),
  );

  await page.goto(`/tasks/${taskId}`);
  await expect(
    page.getByText(
      "The evidence-backed supplier quality analysis is complete.",
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "supplier-quality-report.pdf" }),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByText(taskText).last()).toBeVisible();
  await page.getByRole("button", { name: "Evidence", exact: true }).click();
  await expect(
    page
      .getByRole("dialog", { name: "Evidence" })
      .getByText("Approved supplier-quality policy evidence."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close Evidence" }).click();
  await page.getByRole("button", { name: "Execution" }).click();
  await expect(
    page
      .getByRole("dialog", { name: "Execution details" })
      .getByText("Retrieve approved supplier-quality policy evidence."),
  ).toBeVisible();
});

test("sidebar switches independent tasks and refresh preserves the selected task", async ({
  page,
}) => {
  const secondTaskId = "T-WORKSPACE-E2E-002";
  const secondTaskText =
    "Analyze Accounts Payable invoices for LE-CN-01 during August 2026.";
  const secondTask = {
    ...completedTask(),
    task_id: secondTaskId,
    trace_id: "TRACE-WORKSPACE-E2E-002",
    task_type: "accounts_payable_analysis.v1",
    task_summary: secondTaskText,
    interaction_projection: {
      ...completedTask().interaction_projection,
      initial_user_message: {
        display_text: secondTaskText,
        created_at: "2026-09-01T09:00:00Z",
      },
      result: {
        final_status: "COMPLETED",
        safe_summary:
          "The evidence-backed Accounts Payable analysis is complete.",
      },
    },
  };

  await page.route("**/api/v1/tasks?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            task_id: secondTaskId,
            task_summary: secondTaskText,
            status: "COMPLETED",
            runtime_status: "FINISHED",
            task_type: "accounts_payable_analysis.v1",
            created_at: "2026-09-01T09:00:00Z",
          },
          {
            task_id: taskId,
            task_summary: taskText,
            status: "COMPLETED",
            runtime_status: "FINISHED",
            task_type: "supplier_quality_analysis.v1",
            created_at: "2026-09-01T08:00:00Z",
          },
        ],
        total: 2,
        limit: 40,
        offset: 0,
      }),
    }),
  );
  await page.route(`**/api/v1/tasks/${taskId}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(completedTask()),
    }),
  );
  await page.route(`**/api/v1/tasks/${secondTaskId}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(secondTask),
    }),
  );
  for (const id of [taskId, secondTaskId]) {
    await page.route(`**/api/v1/tasks/${id}/artifacts`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ task_id: id, artifacts: [] }),
      }),
    );
  }

  await page.goto(`/tasks/${taskId}`);
  await page.getByRole("link", { name: new RegExp(secondTaskText) }).click();
  await expect(page).toHaveURL(new RegExp(`/tasks/${secondTaskId}$`));
  await expect(
    page.getByText(secondTask.interaction_projection.result.safe_summary),
  ).toBeVisible();

  await page.reload();
  await expect(page).toHaveURL(new RegExp(`/tasks/${secondTaskId}$`));
  await expect(page.getByText(secondTaskText).last()).toBeVisible();

  await page.getByRole("link", { name: new RegExp(taskText) }).click();
  await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}$`));
  await expect(
    page.getByText(completedTask().interaction_projection.result.safe_summary),
  ).toBeVisible();
});

test("mobile task history is a dismissible drawer", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await routeHistory(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Open task history" }).click();
  const drawer = page.getByRole("dialog", { name: "Task history" });
  await expect(drawer).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(
    page.getByRole("button", { name: "Open task history" }),
  ).toBeFocused();
});
