import { expect, test } from "@playwright/test";

const taskText =
  "Analyze supplier quality for Q2 2026, compare with Q1, check the approved quality policy, and generate a PDF report.";

test("AP clarification keeps one task through two rounds and exposes its PDF", async ({
  page,
}) => {
  const taskId = "T-AP-CLAR-E2E-001";
  const traceId = "TRACE-AP-CLAR-E2E-001";
  let round = 0;
  const submittedClarifications: unknown[] = [];
  const task = () => ({
    task_id: taskId,
    trace_id: traceId,
    status: round < 2 ? "WAITING_CLARIFICATION" : "COMPLETED",
    runtime_status: round < 2 ? "SUSPENDED" : "FINISHED",
    task_type: "accounts_payable_analysis.v1",
    created_at: "2026-09-01T08:00:00Z",
    started_at: "2026-09-01T08:00:01Z",
    completed_at: round < 2 ? null : "2026-09-01T08:00:08Z",
    cancelled_at: null,
    current_step: null,
    task_summary:
      "Analyze recent Accounts Payable invoices and generate a PDF report.",
    pending_approval_id: null,
    pending_clarification:
      round === 0
        ? {
            clarification_id: "CLAR-E2E-001",
            round: 1,
            created_at: "2026-09-01T08:00:01Z",
            questions: [
              {
                field: "time_range",
                reason: "An explicit invoice date range is required.",
                prompt: "What exact start and end dates should be analyzed?",
                input_type: "date_range",
                required: true,
                allowed_values: [],
                constraints: { format: "YYYY-MM-DD" },
              },
              {
                field: "legal_entity_ids",
                reason: "Choose within the caller's current scope.",
                prompt: "Which authorized legal entity should be analyzed?",
                input_type: "single_select",
                required: true,
                allowed_values: ["LE-CN-01", "LE-DE-01"],
                constraints: {},
              },
            ],
          }
        : round === 1
          ? {
              clarification_id: "CLAR-E2E-002",
              round: 2,
              created_at: "2026-09-01T08:00:03Z",
              questions: [
                {
                  field: "legal_entity_ids",
                  reason: "Choose within the caller's current scope.",
                  prompt: "Which authorized legal entity should be analyzed?",
                  input_type: "single_select",
                  required: true,
                  allowed_values: ["LE-CN-01", "LE-DE-01"],
                  constraints: {},
                },
              ],
            }
          : null,
    step_count: round < 2 ? 0 : 14,
    evidence_count: round < 2 ? 0 : 20,
    artifact_count: round < 2 ? 0 : 1,
    error_summary: null,
  });

  await page.route("**/api/v1/tasks", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    expect(route.request().postDataJSON()).toMatchObject({
      task: "Analyze recent Accounts Payable invoices and generate a PDF report.",
      task_type: "accounts_payable_analysis.v1",
    });
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
      body: JSON.stringify(task()),
    }),
  );
  await page.route(`**/api/v1/tasks/${taskId}/steps`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ task_id: taskId, steps: [] }),
    }),
  );
  await page.route(
    `**/api/v1/tasks/${taskId}/clarifications/*`,
    async (route) => {
      submittedClarifications.push(route.request().postDataJSON());
      round += 1;
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          clarification_id: round === 1 ? "CLAR-E2E-001" : "CLAR-E2E-002",
          clarification_status: "SUBMITTED",
          task_id: taskId,
          task_status: "UNDERSTANDING",
          runtime_status: "READY",
          status_url: `/v1/tasks/${taskId}`,
          accepted_at: "2026-09-01T08:00:03Z",
          trace_id: traceId,
          reused: false,
        }),
      });
    },
  );
  await page.route(`**/api/v1/tasks/${taskId}/artifacts`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        task_id: taskId,
        artifacts: [
          {
            artifact_id: "A-AP-CLAR-E2E-001",
            task_id: taskId,
            format: "PDF",
            filename: "accounts-payable-report.pdf",
            media_type: "application/pdf",
            checksum: "sha256:ap-clarification-e2e",
            size_bytes: 8192,
            created_at: "2026-09-01T08:00:08Z",
          },
        ],
      }),
    }),
  );

  await page.goto("/tasks/new");
  await page
    .getByLabel("Use case")
    .selectOption("accounts_payable_analysis.v1");
  await page
    .getByLabel("What do you want the Agent to do?")
    .fill(
      "Analyze recent Accounts Payable invoices and generate a PDF report.",
    );
  await page.getByRole("button", { name: "Submit task" }).click();

  await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}$`));
  await expect(page.getByText("Information needed · Round 1")).toBeVisible();
  await page.getByLabel("Start date", { exact: true }).fill("2026-08-01");
  await page.getByLabel("End date", { exact: true }).fill("2026-08-31");
  await page
    .getByRole("button", { name: "Submit information and resume" })
    .click();

  await expect(page.getByText("Information needed · Round 2")).toBeVisible();
  await page
    .getByLabel("Which authorized legal entity should be analyzed?")
    .selectOption("LE-CN-01");
  await page
    .getByRole("button", { name: "Submit information and resume" })
    .click();

  await expect(page.getByText("COMPLETED").first()).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}$`));
  expect(submittedClarifications).toEqual([
    {
      answers: {
        time_range: { start_date: "2026-08-01", end_date: "2026-08-31" },
      },
      message: null,
    },
    { answers: { legal_entity_ids: "LE-CN-01" }, message: null },
  ]);
  await page.getByRole("link", { name: /Report \(1\)/ }).click();
  await expect(page.getByText("accounts-payable-report.pdf")).toBeVisible();
});

test("Accounts Payable selector, badge, and safe report summary share the existing console", async ({
  page,
}) => {
  const apTask = {
    task_id: "T-AP-E2E-001",
    trace_id: "TRACE-AP-E2E-001",
    status: "COMPLETED",
    runtime_status: "FINISHED",
    task_type: "accounts_payable_analysis.v1",
    created_at: "2026-08-23T08:00:00Z",
    started_at: "2026-08-23T08:00:00Z",
    completed_at: "2026-08-23T08:00:05Z",
    cancelled_at: null,
    current_step: null,
    task_summary: "Analyze Accounts Payable exceptions for Q2 2026.",
    pending_approval_id: null,
    pending_clarification: null,
    step_count: 14,
    evidence_count: 20,
    artifact_count: 1,
    error_summary: null,
  };
  await page.route("**/api/v1/tasks", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    const body: unknown = route.request().postDataJSON();
    expect(body).toMatchObject({
      task_type: "accounts_payable_analysis.v1",
    });
    expect(body).not.toHaveProperty("tenant_id");
    expect(body).not.toHaveProperty("roles");
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        task_id: apTask.task_id,
        trace_id: apTask.trace_id,
        task_status: "CREATED",
        runtime_status: "READY",
        accepted_at: apTask.created_at,
        status_url: `/v1/tasks/${apTask.task_id}`,
        artifacts_url: `/v1/tasks/${apTask.task_id}/artifacts`,
      }),
    });
  });
  await page.route(`**/api/v1/tasks/${apTask.task_id}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(apTask),
    }),
  );
  await page.route(`**/api/v1/tasks/${apTask.task_id}/steps`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ task_id: apTask.task_id, steps: [] }),
    }),
  );
  await page.route(`**/api/v1/tasks/${apTask.task_id}/artifacts`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        task_id: apTask.task_id,
        artifacts: [
          {
            artifact_id: "A-AP-E2E-001",
            task_id: apTask.task_id,
            format: "JSON",
            filename: "accounts-payable-report.json",
            media_type: "application/json",
            checksum: "sha256:ap-e2e",
            size_bytes: 4096,
            created_at: apTask.completed_at,
          },
        ],
      }),
    }),
  );

  await page.goto("/tasks/new");
  await page
    .getByLabel("Use case")
    .selectOption("accounts_payable_analysis.v1");
  await page
    .getByLabel("What do you want the Agent to do?")
    .fill("Analyze Accounts Payable exceptions from 2026-04-01 to 2026-06-30.");
  await page.getByRole("button", { name: "Submit task" }).click();

  await expect(
    page.getByText("Accounts Payable", { exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: /Report/ }).click();
  await expect(
    page.getByRole("heading", { name: "Verified report summary" }),
  ).toBeVisible();
  await expect(page.getByText("4.00 KB").first()).toBeVisible();
});

test("real API task completes with traceable Evidence and a downloadable Artifact", async ({
  page,
}) => {
  await page.goto("/tasks/new");
  await page.getByLabel("What do you want the Agent to do?").fill(taskText);
  await page.getByRole("button", { name: "Submit task" }).click();

  await expect(
    page.getByText("Verification passed and the final result was committed."),
  ).toBeVisible();
  await expect(page.getByText("COMPLETED").first()).toBeVisible();

  await page.getByRole("link", { name: /Evidence/ }).click();
  await expect(
    page.getByRole("heading", { name: "Evidence ledger view" }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("heading", { name: "Approved enterprise document evidence" })
      .first(),
  ).toBeVisible();
  await expect(
    page.getByText("Governed read-only query evidence"),
  ).toBeVisible();
  await expect(
    page.getByText("Deterministic derived calculation evidence"),
  ).toBeVisible();

  await page.getByRole("link", { name: /Report/ }).click();
  await expect(
    page.getByRole("heading", { name: "Verified Artifacts" }),
  ).toBeVisible();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download PDF" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.pdf$/);
});

test("authorized approver inspects and approves the frozen action", async ({
  page,
}) => {
  await page.goto("/tasks/new");
  await page.getByLabel("What do you want the Agent to do?").fill(taskText);
  await page
    .getByRole("checkbox", { name: /Require governed approval/ })
    .check();
  await page.getByRole("button", { name: "Submit task" }).click();

  await expect(
    page.getByText("Execution is waiting for governed approval"),
  ).toBeVisible();
  await page.getByRole("link", { name: "Open approval workbench" }).click();
  await expect(page.getByText("Frozen proposed arguments")).toBeVisible();
  await expect(page.getByText("row_limit", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Approve action" }).click();

  await expect(
    page.getByText("Verification passed and the final result was committed."),
  ).toBeVisible();
});

test("an approver can reject a controlled action with a reason", async ({
  page,
}) => {
  await page.goto("/tasks/new");
  await page.getByLabel("What do you want the Agent to do?").fill(taskText);
  await page
    .getByRole("checkbox", { name: /Require governed approval/ })
    .check();
  await page.getByRole("button", { name: "Submit task" }).click();
  await page.getByRole("link", { name: "Open approval workbench" }).click();

  await page.getByRole("button", { name: "Reject" }).click();
  await page
    .getByLabel(/Decision reason/)
    .fill("The requested data action is not approved.");
  await page.getByRole("button", { name: "Reject action" }).click();

  await expect(
    page.getByText(
      "Execution was cancelled and cannot leave the terminal state.",
    ),
  ).toBeVisible();
});

test("task cancellation requires explicit confirmation", async ({ page }) => {
  await page.goto("/tasks/new");
  await page.getByLabel("What do you want the Agent to do?").fill(taskText);
  await page
    .getByRole("checkbox", { name: /Require governed approval/ })
    .check();
  await page.getByRole("button", { name: "Submit task" }).click();

  await page.getByRole("button", { name: "Cancel task" }).click();
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("heading", { name: "Cancel this task?" }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel task" }).click();

  await expect(
    page.getByText(
      "Execution was cancelled and cannot leave the terminal state.",
    ),
  ).toBeVisible();
});

test("uniform API failures are rendered with trace context", async ({
  page,
}) => {
  await page.route("**/api/v1/tasks", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        error_code: "SYSTEM_NOT_READY",
        message: "The governed runtime is temporarily not ready.",
        task_id: null,
        trace_id: "TRACE-E2E-FAILURE",
        details: {},
      }),
    });
  });
  await page.goto("/tasks/new");
  await page.getByLabel("What do you want the Agent to do?").fill(taskText);
  await page.getByRole("button", { name: "Submit task" }).click();

  await expect(
    page.getByText("The governed runtime is temporarily not ready."),
  ).toBeVisible();
  await expect(page.getByText("TRACE-E2E-FAILURE")).toBeVisible();
});
