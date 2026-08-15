import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderApp } from "../test/render";

describe("Evidence UX", () => {
  it("renders Document, Database, and Calculation metadata and lineage", async () => {
    renderApp("/tasks/T-TEST-001/evidence");
    expect(
      await screen.findByText("Approved enterprise document evidence"),
    ).toBeVisible();
    expect(screen.getByText("Governed read-only query evidence")).toBeVisible();
    expect(
      screen.getByText("Deterministic derived calculation evidence"),
    ).toBeVisible();
    expect(screen.getByText("sha256:query-fingerprint")).toBeVisible();
    expect(screen.getByText("defect_count / inspected_count")).toBeVisible();
    expect(screen.getAllByText("EV-DB-01").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Not exposed").length).toBeGreaterThan(0);
  });
});
