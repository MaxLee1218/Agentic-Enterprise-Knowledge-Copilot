import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { createdTask } from "../test/fixtures";
import { renderApp } from "../test/render";
import { server } from "../test/server";

describe("task creation", () => {
  it("shows client validation for an empty task", async () => {
    const user = userEvent.setup();
    renderApp("/tasks/new");
    await user.click(screen.getByRole("button", { name: "Run task" }));
    expect(
      await screen.findByText("Describe the enterprise task to run."),
    ).toBeVisible();
  });

  it("submits supported fields and redirects to task detail", async () => {
    const user = userEvent.setup();
    const requestBody = vi.fn();
    server.use(
      http.post("*/api/v1/tasks", async ({ request }) => {
        requestBody(await request.json());
        return HttpResponse.json(createdTask, { status: 201 });
      }),
    );
    renderApp("/tasks/new");
    await user.type(
      screen.getByLabelText("What do you want the Agent to do?"),
      "Analyze supplier quality for Q2 2026 and generate a PDF report.",
    );
    await user.click(screen.getByRole("button", { name: "Run task" }));
    expect(await screen.findByText("Governed task")).toBeVisible();
    expect(requestBody).toHaveBeenCalledWith(
      expect.objectContaining({ output_format: "pdf" }),
    );
  });

  it("renders the uniform server validation error", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("*/api/v1/tasks", () =>
        HttpResponse.json(
          {
            error_code: "INVALID_TASK_INPUT",
            message: "Task text is invalid.",
            task_id: null,
            trace_id: "TRACE-ERROR",
            details: {},
          },
          { status: 422 },
        ),
      ),
    );
    renderApp("/tasks/new");
    await user.type(
      screen.getByLabelText("What do you want the Agent to do?"),
      "Analyze Q2 2026 supplier quality.",
    );
    await user.click(screen.getByRole("button", { name: "Run task" }));
    expect(await screen.findByText("Task text is invalid.")).toBeVisible();
    expect(screen.getByText("TRACE-ERROR")).toBeVisible();
  });
});
