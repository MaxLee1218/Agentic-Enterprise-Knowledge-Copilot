import { expect, test } from "@playwright/test";

const taskText =
  "Analyze supplier quality for Q2 2026, compare with Q1, check the approved quality policy, and generate a PDF report.";

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
