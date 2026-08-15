import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { waitingTask } from "../test/fixtures";
import { renderApp } from "../test/render";
import { server } from "../test/server";

describe("task lifecycle controls", () => {
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
});
