import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { renderApp } from "../test/render";
import { server } from "../test/server";
import {
  accountsPayableArtifacts,
  accountsPayableTask,
} from "../test/fixtures";

describe("Artifact flow", () => {
  it("renders PDF and JSON metadata with guarded download links", async () => {
    renderApp("/tasks/T-TEST-001/report");
    const pdf = await screen.findByRole("link", { name: "Download PDF" });
    const json = screen.getByRole("link", { name: "Download JSON" });
    expect(pdf).toHaveAttribute(
      "href",
      "/api/v1/tasks/T-TEST-001/artifacts/A-PDF-01",
    );
    expect(json).toHaveAttribute(
      "href",
      "/api/v1/tasks/T-TEST-001/artifacts/A-JSON-01",
    );
    expect(screen.getByText("sha256:pdf-checksum")).toBeVisible();
  });

  it("does not claim an Artifact exists when the list is empty", async () => {
    server.use(
      http.get("*/api/v1/tasks/:taskId/artifacts", () =>
        HttpResponse.json({ task_id: "T-TEST-001", artifacts: [] }),
      ),
    );
    renderApp("/tasks/T-TEST-001/report");
    expect(
      await screen.findByText("No verified Artifact available"),
    ).toBeVisible();
  });

  it("renders an AP summary from safe Artifact metadata only", async () => {
    server.use(
      http.get("*/api/v1/tasks/:taskId", () =>
        HttpResponse.json(accountsPayableTask),
      ),
      http.get("*/api/v1/tasks/:taskId/artifacts", () =>
        HttpResponse.json({
          task_id: accountsPayableTask.task_id,
          artifacts: accountsPayableArtifacts,
        }),
      ),
    );
    renderApp(`/tasks/${accountsPayableTask.task_id}/report`);
    expect(
      await screen.findByRole("heading", { name: "Verified report summary" }),
    ).toBeVisible();
    expect(screen.getAllByText("4.00 KB")).toHaveLength(2);
    expect(screen.queryByText(/invoice_number/)).not.toBeInTheDocument();
    expect(screen.queryByText(/gross_amount/)).not.toBeInTheDocument();
  });
});
