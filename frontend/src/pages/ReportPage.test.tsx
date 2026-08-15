import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { renderApp } from "../test/render";
import { server } from "../test/server";

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
});
