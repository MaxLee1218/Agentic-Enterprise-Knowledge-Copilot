import { expect, test } from "@playwright/test";

test("real API and Worker resolve year2025 CN and enter planning on the same task", async ({
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
    page.getByText("What exact period would you like me to analyze?"),
  ).toBeVisible({ timeout: 30_000 });
  const taskUrl = page.url();
  const taskId = taskUrl.split("/").at(-1);
  expect(taskId).toBeTruthy();
  await expect(page.getByRole("status")).toHaveText("Waiting for information");
  await composer.fill("year2025 CN");
  await composer.press("Enter");

  await expect(
    page.getByText("Building and validating a governed execution plan."),
  ).toBeVisible({
    timeout: 60_000,
  });
  await expect(page).toHaveURL(taskUrl);
  await expect(
    page
      .getByRole("complementary", { name: "Task history" })
      .locator(`a[href="${new URL(taskUrl).pathname}"]`)
      .getByText(/Planning|Running|Verifying|Completed|Failed/, {
        exact: true,
      }),
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

  await expect(page.getByRole("status")).toHaveText("Completed", {
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

test("real API persists confirmation, rejection, and correction on one task", async ({
  page,
}) => {
  test.setTimeout(90_000);
  await page.goto("/");
  const composer = page.getByRole("textbox", {
    name: "Message the Enterprise Knowledge Copilot",
  });
  await composer.fill(
    "Analyze recent Accounts Payable invoices and generate a JSON report.",
  );
  await composer.press("Enter");
  await expect(
    page.getByText("What exact period would you like me to analyze?"),
  ).toBeVisible({ timeout: 30_000 });
  const taskUrl = page.url();

  await composer.fill("Q2 2026 China");
  await composer.press("Enter");
  await expect(
    page.getByText("Please confirm this interpretation before I continue."),
  ).toBeVisible({ timeout: 30_000 });
  await expect(
    page.getByText(/I understood that as legal entity LE-CN-01/),
  ).toBeVisible();

  await page.reload();
  await expect(page).toHaveURL(taskUrl);
  await expect(
    page.getByText("Please confirm this interpretation before I continue."),
  ).toBeVisible();
  await composer.fill("no");
  await composer.press("Enter");
  await expect(
    page.getByText(
      /I understood the period as April 1, 2026 through June 30, 2026/,
    ),
  ).toBeVisible({ timeout: 30_000 });

  await composer.fill("Actually use LE-CN-01.");
  await composer.press("Enter");
  await expect(page.getByRole("status")).toHaveText("Completed", {
    timeout: 60_000,
  });
  await expect(page).toHaveURL(taskUrl);
});

test("real API denies an unauthorized natural entity before planning and tools", async ({
  page,
}) => {
  test.setTimeout(60_000);
  await page.goto("/");
  const composer = page.getByRole("textbox", {
    name: "Message the Enterprise Knowledge Copilot",
  });
  await composer.fill("Analyze recent Accounts Payable invoices.");
  await composer.press("Enter");
  await expect(
    page.getByText("What exact period would you like me to analyze?"),
  ).toBeVisible({ timeout: 30_000 });

  await composer.fill("Q2 2026 LE-US-01");
  await composer.press("Enter");
  await expect(page.getByRole("status")).toHaveText("Failed", {
    timeout: 30_000,
  });
  await expect(
    page.getByText("Building and validating a governed execution plan."),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Execution" }).click();
  await expect(
    page.getByText("No plan steps are available yet."),
  ).toBeVisible();
});
