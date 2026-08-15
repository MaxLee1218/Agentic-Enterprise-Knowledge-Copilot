import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { renderApp } from "../test/render";
import { server } from "../test/server";

describe("execution steps", () => {
  it("shows success, failure, retry, dependency, error, and Evidence detail", async () => {
    renderApp("/tasks/T-TEST-001");
    expect(await screen.findByText("knowledge_search")).toBeVisible();
    expect(screen.getByText("TECHNICAL FAILURE")).toBeVisible();
    expect(screen.getByText("2 (1 retries)")).toBeVisible();
    expect(screen.getByText("DATABASE_UNAVAILABLE")).toBeVisible();
    expect(screen.getByText("EV-DOC-01")).toBeVisible();
  });

  it("renders an explicit empty plan", async () => {
    server.use(
      http.get("*/api/v1/tasks/:taskId/steps", () =>
        HttpResponse.json({ task_id: "T-TEST-001", steps: [] }),
      ),
    );
    renderApp("/tasks/T-TEST-001");
    expect(await screen.findByText("No persisted plan steps")).toBeVisible();
  });
});
