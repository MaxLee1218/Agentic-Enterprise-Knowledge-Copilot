import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { renderApp } from "../test/render";
import { clarificationTask } from "../test/fixtures";
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

describe("interactive clarification", () => {
  it("renders a waiting badge, questions, authorized choices, and zero-step guidance", async () => {
    server.use(
      http.get("*/api/v1/tasks/:taskId", () =>
        HttpResponse.json(clarificationTask),
      ),
      http.get("*/api/v1/tasks/:taskId/steps", () =>
        HttpResponse.json({ task_id: clarificationTask.task_id, steps: [] }),
      ),
    );

    renderApp("/tasks/T-TEST-001");

    expect(
      (await screen.findAllByText("Waiting for information")).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getByRole("heading", {
        name: "Agent needs more information before planning",
      }),
    ).toBeVisible();
    expect(screen.getByLabelText("Start date")).toHaveAttribute("type", "date");
    expect(screen.getByRole("option", { name: "LE-CN-01" })).toBeVisible();
    expect(
      await screen.findByText("Execution has not started yet"),
    ).toBeVisible();
  });

  it("accepts a partial date answer and resumes the same task asynchronously", async () => {
    const user = userEvent.setup();
    let submittedBody: unknown;
    let currentTask = clarificationTask;
    server.use(
      http.get("*/api/v1/tasks/:taskId", () => HttpResponse.json(currentTask)),
      http.get("*/api/v1/tasks/:taskId/steps", () =>
        HttpResponse.json({ task_id: clarificationTask.task_id, steps: [] }),
      ),
      http.post(
        "*/api/v1/tasks/:taskId/clarifications/:clarificationId",
        async ({ request }) => {
          submittedBody = await request.json();
          currentTask = {
            ...clarificationTask,
            status: "PLANNING",
            runtime_status: "LEASED",
            pending_clarification: null,
          };
          return HttpResponse.json(
            {
              clarification_id: "CLAR-TEST-001",
              clarification_status: "SUBMITTED",
              task_id: clarificationTask.task_id,
              task_status: "UNDERSTANDING",
              runtime_status: "READY",
              status_url: `/v1/tasks/${clarificationTask.task_id}`,
              accepted_at: "2026-08-13T08:01:00Z",
              trace_id: clarificationTask.trace_id,
              reused: false,
            },
            { status: 202 },
          );
        },
      ),
    );

    renderApp("/tasks/T-TEST-001");
    await user.type(await screen.findByLabelText("Start date"), "2026-08-01");
    await user.type(screen.getByLabelText("End date"), "2026-08-31");
    await user.click(
      screen.getByRole("button", { name: "Submit information and resume" }),
    );

    expect((await screen.findAllByText("PLANNING")).length).toBeGreaterThan(0);
    expect(submittedBody).toEqual({
      answers: {
        time_range: {
          start_date: "2026-08-01",
          end_date: "2026-08-31",
        },
      },
      message: null,
    });
  });

  it("accepts a natural-language-only response and surfaces stale conflicts", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("*/api/v1/tasks/:taskId", () =>
        HttpResponse.json(clarificationTask),
      ),
      http.get("*/api/v1/tasks/:taskId/steps", () =>
        HttpResponse.json({ task_id: clarificationTask.task_id, steps: [] }),
      ),
      http.post("*/api/v1/tasks/:taskId/clarifications/:clarificationId", () =>
        HttpResponse.json(
          {
            error_code: "CLARIFICATION_STALE",
            message: "This clarification is no longer pending.",
            task_id: clarificationTask.task_id,
            trace_id: clarificationTask.trace_id,
            details: {},
          },
          { status: 409 },
        ),
      ),
    );

    renderApp("/tasks/T-TEST-001");
    await user.type(
      await screen.findByLabelText("Answer in your own words Optional"),
      "Use August 2026 for LE-CN-01.",
    );
    await user.click(
      screen.getByRole("button", { name: "Submit information and resume" }),
    );

    expect(
      await screen.findByText("Clarification response was not accepted"),
    ).toBeVisible();
    expect(screen.getByText("CLARIFICATION_STALE")).toBeVisible();
  });
});
