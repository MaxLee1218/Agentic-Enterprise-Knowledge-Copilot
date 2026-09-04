import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { renderApp } from "../test/render";
import {
  approval,
  clarificationTask,
  createdTask,
  task,
  waitingTask,
} from "../test/fixtures";
import { server } from "../test/server";

describe("Chat-first Task Workspace", () => {
  it("renders the deterministic welcome, history sidebar, and no execution selectors", async () => {
    renderApp("/");
    expect(
      await screen.findByRole("heading", {
        name: "Welcome to the Enterprise Knowledge Copilot.",
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("textbox", {
        name: "Message the Enterprise Knowledge Copilot",
      }),
    ).toBeEnabled();
    expect(screen.queryByLabelText(/task type/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/output format/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/maximum steps/i)).not.toBeInTheDocument();
    expect(await screen.findByText(task.task_summary)).toBeVisible();
  });

  it("submits only natural-language task text with an Idempotency-Key", async () => {
    const user = userEvent.setup();
    let requestBody: unknown;
    let idempotencyKey: string | null = null;
    server.use(
      http.post("*/api/v1/tasks", async ({ request }) => {
        requestBody = await request.json();
        idempotencyKey = request.headers.get("Idempotency-Key");
        return HttpResponse.json(createdTask, { status: 202 });
      }),
    );
    renderApp("/");
    const composer = screen.getByRole("textbox", {
      name: "Message the Enterprise Knowledge Copilot",
    });
    await user.type(
      composer,
      "Analyze supplier quality issues in Q2 and create a PDF report.{Enter}",
    );
    await screen.findByRole("heading", { name: /Analyze supplier quality/ });
    expect(requestBody).toEqual({
      task: "Analyze supplier quality issues in Q2 and create a PDF report.",
    });
    expect(idempotencyKey).toMatch(/^[0-9a-f-]{36}$/i);
  });

  it("preserves a rejected draft and renders the typed unsupported response", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("*/api/v1/tasks", () =>
        HttpResponse.json(
          {
            error_code: "UNSUPPORTED_TASK_TYPE",
            message: "This task is not currently supported.",
            task_id: null,
            trace_id: "TRACE-UNSUPPORTED",
            details: {},
          },
          { status: 422 },
        ),
      ),
    );
    renderApp("/");
    const composer = screen.getByRole("textbox", {
      name: "Message the Enterprise Knowledge Copilot",
    });
    await user.type(composer, "Send an email to a supplier.{Enter}");
    expect(
      await screen.findByText("This task is not currently supported."),
    ).toBeVisible();
    expect(composer).toHaveValue("Send an email to a supplier.");
    expect(screen.getByText("UNSUPPORTED_TASK_TYPE")).toBeInTheDocument();
  });

  it("does not send Enter while an IME composition is active", () => {
    const request = vi.fn();
    server.use(http.post("*/api/v1/tasks", request));
    renderApp("/");
    const composer = screen.getByRole("textbox", {
      name: "Message the Enterprise Knowledge Copilot",
    });
    fireEvent.change(composer, { target: { value: "分析供应商质量" } });
    fireEvent.compositionStart(composer);
    fireEvent.keyDown(composer, {
      key: "Enter",
      code: "Enter",
      isComposing: true,
    });
    fireEvent.compositionEnd(composer);
    expect(request).not.toHaveBeenCalled();
  });

  it("reconstructs messages and lazy-loads Evidence and execution drawers", async () => {
    const user = userEvent.setup();
    renderApp(`/tasks/${task.task_id}`);
    expect(
      await screen.findByText(
        task.interaction_projection.initial_user_message.display_text,
      ),
    ).toBeVisible();
    expect(
      screen.getByText(task.interaction_projection.result?.safe_summary ?? ""),
    ).toBeVisible();
    expect(
      screen.getByRole("textbox", {
        name: "Message the Enterprise Knowledge Copilot",
      }),
    ).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Evidence" }));
    const evidenceDrawer = await screen.findByRole("dialog", {
      name: "Evidence",
    });
    expect(
      within(evidenceDrawer).getByText(/Approved policy defines/),
    ).toBeVisible();
    await user.click(
      within(evidenceDrawer).getByRole("button", { name: "Close Evidence" }),
    );

    await user.click(screen.getByRole("button", { name: "Execution" }));
    const executionDrawer = await screen.findByRole("dialog", {
      name: "Execution details",
    });
    expect(
      within(executionDrawer).getByText(
        /Retrieve approved supplier-quality policy/,
      ),
    ).toBeVisible();
  });

  it("answers a clarification in natural language on the same task", async () => {
    const user = userEvent.setup();
    let body: unknown;
    server.use(
      http.get("*/api/v1/tasks/:taskId", () =>
        HttpResponse.json(clarificationTask),
      ),
      http.post(
        "*/api/v1/tasks/:taskId/clarifications/:clarificationId",
        async ({ request }) => {
          body = await request.json();
          return HttpResponse.json({
            task_id: clarificationTask.task_id,
            clarification_id: "CLAR-TEST-001",
            clarification_status: "SUBMITTED",
            task_status: "UNDERSTANDING",
            runtime_status: "READY",
            trace_id: clarificationTask.trace_id,
            status_url: `/v1/tasks/${clarificationTask.task_id}`,
            accepted_at: "2026-08-13T08:01:00Z",
            reused: false,
          });
        },
      ),
    );
    renderApp(`/tasks/${clarificationTask.task_id}`);
    expect(
      await screen.findByText(
        "What exact start and end dates should be analyzed?",
      ),
    ).toBeVisible();
    const composer = screen.getByRole("textbox", {
      name: "Message the Enterprise Knowledge Copilot",
    });
    await user.type(
      composer,
      "Use 2026-04-01 through 2026-06-30 for LE-CN-01.{Enter}",
    );
    await waitFor(() =>
      expect(body).toEqual({
        message: "Use 2026-04-01 through 2026-06-30 for LE-CN-01.",
      }),
    );
    expect(composer).toHaveValue("");
  });

  it("renders an explicit approval card and submits an approve decision", async () => {
    const user = userEvent.setup();
    let body: unknown;
    server.use(
      http.get("*/api/v1/tasks/:taskId", () => HttpResponse.json(waitingTask)),
      http.get("*/api/v1/tasks/:taskId/approvals/:approvalId", () =>
        HttpResponse.json(approval),
      ),
      http.post(
        "*/api/v1/tasks/:taskId/approvals/:approvalId",
        async ({ request }) => {
          body = await request.json();
          return HttpResponse.json({
            task_id: waitingTask.task_id,
            approval_id: approval.approval_id,
            approval_status: "APPROVED",
            resolution_action: "APPROVE",
            task_status: "EXECUTING",
            runtime_status: "READY",
            trace_id: waitingTask.trace_id,
            status_url: `/v1/tasks/${waitingTask.task_id}`,
            accepted_at: "2026-08-13T08:02:00Z",
          });
        },
      ),
    );
    renderApp(`/tasks/${waitingTask.task_id}`);
    expect(
      await screen.findByRole("heading", {
        name: "Review a controlled action",
      }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Approve" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Edit" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Reject" })).toBeVisible();
    expect(
      screen.getByRole("textbox", {
        name: "Message the Enterprise Knowledge Copilot",
      }),
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Approve action" }));
    await waitFor(() => expect(body).toEqual({ action: "approve" }));
  });

  it("requires confirmation before cancelling a running task", async () => {
    const user = userEvent.setup();
    const running = {
      ...waitingTask,
      status: "EXECUTING" as const,
      runtime_status: "LEASED" as const,
      pending_approval_id: null,
      interaction_projection: {
        ...waitingTask.interaction_projection,
        approval_summaries: [],
        phase_events: [
          { phase: "EXECUTING" as const, occurred_at: "2026-08-13T08:00:02Z" },
        ],
      },
    };
    server.use(
      http.get("*/api/v1/tasks/:taskId", () => HttpResponse.json(running)),
      http.post("*/api/v1/tasks/:taskId/cancel", () =>
        HttpResponse.json({ ...running, status: "CANCELLED" }, { status: 202 }),
      ),
    );
    renderApp(`/tasks/${running.task_id}`);
    await user.click(
      await screen.findByRole("button", { name: "Cancel task" }),
    );
    const dialog = screen.getByRole("dialog", { name: "Cancel this task?" });
    expect(dialog).toBeVisible();
    expect(
      within(dialog).getByRole("button", { name: "Cancel task" }),
    ).toBeVisible();
  });
});
