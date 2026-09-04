import { expect, test } from "@playwright/test";

test("real API and Worker driver complete the exact AP clarification demo", async ({
  page,
}) => {
  test.setTimeout(90_000);

  await page.goto("/");
  const composer = page.getByRole("textbox", {
    name: "Message the Enterprise Knowledge Copilot",
  });
  await composer.fill(
    "Analyze recent Accounts Payable invoices and generate a PDF report.",
  );
  await composer.press("Enter");

  await expect(
    page.getByText("What exact start and end dates should be analyzed?"),
  ).toBeVisible({ timeout: 30_000 });
  const taskUrl = page.url();
  const taskId = taskUrl.split("/").at(-1);
  expect(taskId).toBeTruthy();
  await expect(page.getByText("Waiting for information").first()).toBeVisible();
  await expect(page.getByText("Waiting for information").first()).toBeVisible();
  await composer.fill("Use 2026-08-01 through 2026-08-31.");
  await composer.press("Enter");

  await expect(
    page.getByText("Which authorized legal entity should be analyzed?"),
  ).toHaveCount(2, { timeout: 30_000 });
  await expect(page).toHaveURL(taskUrl);
  await composer.fill("Use legal entity LE-CN-01.");
  await composer.press("Enter");

  await expect(page.getByText("COMPLETED").first()).toBeVisible({
    timeout: 60_000,
  });
  await expect(page).toHaveURL(taskUrl);
  await page.getByRole("button", { name: "Execution" }).click();
  await expect(
    page.getByText("Generate the governed internal Accounts Payable report."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close Execution details" }).click();
  await page.getByRole("button", { name: "Evidence", exact: true }).click();
  await expect(page.getByText(/evidence with fields/i).first()).toBeVisible();
  await page.getByRole("button", { name: "Close Evidence" }).click();
  await expect(page.getByRole("heading", { name: /\.pdf$/ })).toBeVisible();
  await expect(page.getByRole("link", { name: "Download PDF" })).toBeVisible();
  await expect(
    page
      .getByRole("complementary", { name: "Task history" })
      .getByText("Completed", { exact: true }),
  ).toBeVisible();
});

test("real API resolves a direct Supplier request without a browser selector", async ({
  page,
}) => {
  test.setTimeout(60_000);
  await page.goto("/");
  const composer = page.getByRole("textbox", {
    name: "Message the Enterprise Knowledge Copilot",
  });
  await composer.fill(
    "Analyze supplier quality deviations for SUP-001 in Q2 2026 and generate a PDF report.",
  );
  await composer.press("Enter");

  await expect(page.getByText("COMPLETED", { exact: true })).toBeVisible({
    timeout: 60_000,
  });
  await expect(
    page.getByText("Running approved analysis steps.", { exact: true }),
  ).toHaveCount(1);
  await expect(
    page.getByText(
      "Supplier quality analysis completed with verified evidence and report.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Download PDF" })).toBeVisible();
});
