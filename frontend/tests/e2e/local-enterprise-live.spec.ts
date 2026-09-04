import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

import { expect, test } from "@playwright/test";

const enabled = process.env.LOCAL_ENTERPRISE_BROWSER_E2E === "1";

test.skip(
  !enabled,
  "Set LOCAL_ENTERPRISE_BROWSER_E2E=1 against the live Local Enterprise topology.",
);
test.setTimeout(360_000);

test("live Accounts Payable browser workflow reaches governed Artifact download", async ({
  page,
}) => {
  await page.goto("/");
  const composer = page.getByRole("textbox", {
    name: "Message the Enterprise Knowledge Copilot",
  });
  await composer.fill(
    "Analyze all Accounts Payable exceptions from 2026-04-01 to 2026-06-30 " +
      "for LE-CN-01 and LE-US-01 and generate a JSON report.",
  );
  await composer.press("Enter");

  await expect(
    page.getByText("Verification passed and the final result was committed."),
  ).toBeVisible({ timeout: 330_000 });
  await page.getByRole("button", { name: "Execution" }).click();
  await expect(
    page.getByRole("dialog", { name: "Execution details" }).locator("li"),
  ).toHaveCount(14);
  await page.getByRole("button", { name: "Close Execution details" }).click();
  await page.getByRole("button", { name: "Evidence", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "Evidence" }).locator("article").first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close Evidence" }).click();
  await expect(page.getByRole("heading", { name: /\.json$/ })).toBeVisible();

  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download JSON" }).click();
  const download = await downloadEvent;
  const downloadedPath = await download.path();
  if (!downloadedPath)
    throw new Error("Playwright did not persist the downloaded Artifact");
  const content = await readFile(downloadedPath);
  const actualChecksum = `sha256:${createHash("sha256").update(content).digest("hex")}`;
  expect(actualChecksum).toMatch(/^sha256:[a-f0-9]{64}$/);

  const report: unknown = JSON.parse(content.toString("utf8"));
  expect(report).toMatchObject({
    exception_summary: {
      metrics: {
        invoice_count: 23,
        exception_invoice_count: 7,
        exception_rate: "0.30434783",
      },
      finding_count: 5,
      warning_count: 2,
    },
    execution_metadata: {
      schema_version: "accounts_payable_report_model.v1",
      template_version: "accounts_payable_report.v1",
      generator_version: "report_generator.v2",
      rule_set_version: "ap_rules.2026.1",
      detail_access: "DETAIL",
    },
  });
  expect(content.toString("utf8")).not.toMatch(
    /"(?:bank_account|iban|swift|tax_id|payment_reference|internal_account_number)"/i,
  );
});
