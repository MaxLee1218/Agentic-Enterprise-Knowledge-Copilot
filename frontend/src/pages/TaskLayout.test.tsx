import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import {
  accountsPayableTask,
  clarificationTask,
  waitingTask,
} from "../test/fixtures";
import { renderApp } from "../test/render";
import { server } from "../test/server";

describe("task lifecycle controls", () => {
  it("shows the task-type badge for Accounts Payable tasks", async () => {
    server.use(
      http.get("*/api/v1/tasks/:taskId", () =>
        HttpResponse.json(accountsPayableTask),
      ),
    );
    renderApp(`/tasks/${accountsPayableTask.task_id}`);
    expect(await screen.findByText("Accounts Payable")).toBeVisible();
  });

  it("requires confirmation and renders the authoritative cancelled state", async () => {
    const user = userEvent.setup();
    let currentTask = waitingTask;
    server.use(
      http.get("*/api/v1/tasks/:taskId", () => HttpResponse.json(currentTask)),
      http.post("*/api/v1/tasks/:taskId/cancel", () => {
        currentTask = {
          ...waitingTask,
          status: "CANCELLED",
          cancelled_at: "2026-08-13T08:02:00Z",
          current_step: null,
          pending_approval_id: null,
          pending_clarification: null,
        };
        return HttpResponse.json(currentTask);
      }),
    );
    renderApp(`/tasks/${waitingTask.task_id}`);

    await screen.findByText("Execution is waiting for governed approval");
    await user.click(screen.getByRole("button", { name: "Cancel task" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Cancel this task?")).toBeVisible();
    await user.click(
      within(dialog).getByRole("button", { name: "Cancel task" }),
    );

    expect(
      await screen.findByText(
        "Execution was cancelled and cannot leave the terminal state.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByText("Execution is waiting for governed approval"),
    ).not.toBeInTheDocument();
  });

  it("allows cancellation while waiting for clarification", async () => {
    const user = userEvent.setup();
    let currentTask = clarificationTask;
    server.use(
      http.get("*/api/v1/tasks/:taskId", () => HttpResponse.json(currentTask)),
      http.get("*/api/v1/tasks/:taskId/steps", () =>
        HttpResponse.json({ task_id: clarificationTask.task_id, steps: [] }),
      ),
      http.post("*/api/v1/tasks/:taskId/cancel", () => {
        currentTask = {
          ...clarificationTask,
          status: "CANCELLED",
          runtime_status: "FINISHED",
          cancelled_at: "2026-08-13T08:02:00Z",
          pending_clarification: null,
        };
        return HttpResponse.json(currentTask);
      }),
    );
    renderApp(`/tasks/${clarificationTask.task_id}`);

    expect(
      (await screen.findAllByText("Waiting for information")).length,
    ).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Cancel task" }));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Cancel task",
      }),
    );

    expect(
      await screen.findByText(
        "Execution was cancelled and cannot leave the terminal state.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByText("Agent needs more information before planning"),
    ).not.toBeInTheDocument();
  });
});
