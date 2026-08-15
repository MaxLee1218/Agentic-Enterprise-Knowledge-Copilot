import { expect, test } from "@playwright/test";

const taskText =
  "Analyze supplier quality for Q2 2026, compare with Q1, check the approved quality policy, and generate a PDF report.";

test("real API task completes with traceable Evidence and a downloadable Artifact", async ({
  page,
}) => {
  await page.goto("/tasks/new");
  await page.getByLabel("What do you want the Agent to do?").fill(taskText);
  await page.getByRole("button", { name: "Run task" }).click();

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
  await page.getByRole("button", { name: "Run task" }).click();

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
  await page.getByRole("button", { name: "Run task" }).click();
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
  await page.getByRole("button", { name: "Run task" }).click();

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
  await page.getByRole("button", { name: "Run task" }).click();

  await expect(
    page.getByText("The governed runtime is temporarily not ready."),
  ).toBeVisible();
  await expect(page.getByText("TRACE-E2E-FAILURE")).toBeVisible();
});
