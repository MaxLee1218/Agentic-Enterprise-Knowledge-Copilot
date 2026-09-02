import { expect, test } from "@playwright/test";

test("real API and Worker driver complete the exact AP clarification demo", async ({
  page,
}) => {
  test.setTimeout(90_000);

  await page.goto("/tasks/new");
  await page
    .getByLabel("Use case")
    .selectOption("accounts_payable_analysis.v1");
  await page.getByLabel("Report format").selectOption("pdf");
  await page
    .getByLabel("What do you want the Agent to do?")
    .fill(
      "Analyze recent Accounts Payable invoices and generate a PDF report.",
    );
  await page.getByRole("button", { name: "Submit task" }).click();

  await expect(page.getByText("Information needed · Round 1")).toBeVisible({
    timeout: 30_000,
  });
  const taskUrl = page.url();
  const taskId = taskUrl.split("/").at(-1);
  expect(taskId).toBeTruthy();
  await expect(page.getByText("Waiting for information").first()).toBeVisible();
  await expect(
    page.getByText("Needs clarification", { exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Start date", { exact: true })).toBeVisible();
  await expect(page.getByLabel("End date", { exact: true })).toBeVisible();
  const entity = page.getByLabel(
    "Which authorized legal entity should be analyzed?",
  );
  await expect(entity.locator("option")).toHaveText([
    "Choose an authorized value",
    "LE-CN-01",
    "LE-DE-01",
  ]);
  await expect(page.getByText("Execution has not started yet")).toBeVisible();

  await page.getByLabel("Start date", { exact: true }).fill("2026-08-01");
  await page.getByLabel("End date", { exact: true }).fill("2026-08-31");
  await page
    .getByRole("button", { name: "Submit information and resume" })
    .click();

  await expect(page.getByText("Information needed · Round 2")).toBeVisible({
    timeout: 30_000,
  });
  await expect(page).toHaveURL(taskUrl);
  await expect(page.getByLabel("Start date", { exact: true })).toHaveCount(0);
  await page
    .getByLabel("Which authorized legal entity should be analyzed?")
    .selectOption("LE-CN-01");
  await page
    .getByRole("button", { name: "Submit information and resume" })
    .click();

  await expect(page.getByText("COMPLETED").first()).toBeVisible({
    timeout: 60_000,
  });
  await expect(page).toHaveURL(taskUrl);
  await expect(
    page.getByText("Accounts Payable", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("14 steps", { exact: true })).toBeVisible();

  await expect(
    page.getByText("report_generator", { exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: /Evidence/ }).click();
  await expect(
    page.getByText("Governed read-only query evidence").first(),
  ).toBeVisible();
  await page.getByRole("link", { name: /Report \(1\)/ }).click();
  await expect(page.getByText(/\.pdf$/)).toBeVisible();
  await expect(page.getByRole("link", { name: "Download PDF" })).toBeVisible();
});
