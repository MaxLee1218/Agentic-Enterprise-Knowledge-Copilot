import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { approval, waitingTask } from "../test/fixtures";
import { renderApp } from "../test/render";
import { server } from "../test/server";

const resolution = {
  approval_id: approval.approval_id,
  approval_status: "RESOLVED",
  resolution_action: "approve",
  resolved_at: "2026-08-13T08:01:00Z",
  resolved_by: "USER-DEMO",
  resume_status: "COMPLETED",
  task_id: waitingTask.task_id,
  task_status: "COMPLETED",
  trace_id: waitingTask.trace_id,
};

function openApproval() {
  server.use(
    http.get("*/api/v1/tasks/:taskId", () => HttpResponse.json(waitingTask)),
  );
  return renderApp(
    `/tasks/${waitingTask.task_id}/approvals/${approval.approval_id}`,
  );
}

describe("approval workbench", () => {
  it("approves the exact frozen action", async () => {
    const user = userEvent.setup();
    const requestBody = vi.fn();
    server.use(
      http.post(
        "*/api/v1/tasks/:taskId/approvals/:approvalId",
        async ({ request }) => {
          requestBody(await request.json());
          return HttpResponse.json(resolution);
        },
      ),
    );
    openApproval();

    expect(await screen.findByText("Frozen proposed arguments")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Approve action" }));

    expect(requestBody).toHaveBeenCalledWith({ action: "approve" });
    expect(await screen.findByText("Current execution state")).toBeVisible();
  });

  it("only submits a decreasing editable value with the complete replacement object", async () => {
    const user = userEvent.setup();
    const requestBody = vi.fn();
    server.use(
      http.post(
        "*/api/v1/tasks/:taskId/approvals/:approvalId",
        async ({ request }) => {
          requestBody(await request.json());
          return HttpResponse.json({
            ...resolution,
            resolution_action: "edit",
          });
        },
      ),
    );
    openApproval();

    await screen.findByText("Frozen proposed arguments");
    await user.click(screen.getByRole("button", { name: "Edit" }));
    const value = screen.getByLabelText("New row_limit");
    await user.clear(value);
    await user.type(value, "9000");
    await user.type(
      screen.getByLabelText(/Decision reason/),
      "Reduce the bounded result set.",
    );
    await user.click(screen.getByRole("button", { name: "Edit action" }));

    expect(requestBody).toHaveBeenCalledWith({
      action: "edit",
      reason: "Reduce the bounded result set.",
      edited_arguments: {
        ...approval.proposed_arguments,
        row_limit: 9000,
      },
    });
  });

  it("requires a rejection reason", async () => {
    const user = userEvent.setup();
    openApproval();
    await screen.findByText("Frozen proposed arguments");
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.click(screen.getByRole("button", { name: "Reject action" }));
    expect(
      await screen.findByText("A rejection reason is required."),
    ).toBeVisible();
  });

  it("shows authorization failures without exposing the controlled action", async () => {
    server.use(
      http.get("*/api/v1/tasks/:taskId/approvals/:approvalId", () =>
        HttpResponse.json(
          {
            error_code: "APPROVAL_FORBIDDEN",
            message: "The current identity is not an authorized approver.",
            task_id: waitingTask.task_id,
            trace_id: waitingTask.trace_id,
            details: {},
          },
          { status: 403 },
        ),
      ),
    );
    openApproval();

    expect(
      await screen.findByText(
        "The current identity is not an authorized approver.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByText("Frozen proposed arguments"),
    ).not.toBeInTheDocument();
  });

  it("shows a concurrent resolution conflict", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("*/api/v1/tasks/:taskId/approvals/:approvalId", () =>
        HttpResponse.json(
          {
            error_code: "APPROVAL_CONFLICT",
            message: "The approval was already resolved by another approver.",
            task_id: waitingTask.task_id,
            trace_id: waitingTask.trace_id,
            details: {},
          },
          { status: 409 },
        ),
      ),
    );
    openApproval();
    await screen.findByText("Frozen proposed arguments");
    await user.click(screen.getByRole("button", { name: "Approve action" }));
    expect(
      await screen.findByText(
        "The approval was already resolved by another approver.",
      ),
    ).toBeVisible();
  });

  it("does not present controls for an expired approval", async () => {
    server.use(
      http.get("*/api/v1/tasks/:taskId/approvals/:approvalId", () =>
        HttpResponse.json({ ...approval, expires_at: "2020-01-01T00:00:00Z" }),
      ),
    );
    openApproval();

    expect(await screen.findByText("Approval cannot be changed")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Approve action" }),
    ).not.toBeInTheDocument();
  });
});
