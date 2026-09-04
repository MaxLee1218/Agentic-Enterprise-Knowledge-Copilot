import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { normalizeThrownError } from "../api/client";
import type { Task } from "../api/types";
import { ApprovalCard } from "../components/ApprovalCard";
import { ArtifactCards } from "../components/ArtifactCards";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DetailDrawer } from "../components/DetailDrawer";
import { ErrorPanel, LoadingState } from "../components/PageState";
import { StatusBadge } from "../components/StatusBadge";
import { TaskComposer } from "../components/TaskComposer";
import {
  useCancelTask,
  useEvidence,
  refreshRelatedOnStatusChange,
  useSteps,
  useSubmitClarification,
  useTask,
} from "../features/tasks/queries";
import { formatDate, formatDuration } from "../utils/format";
import { cancellableTaskStatuses, runtimeLabel } from "../utils/status";

type Drawer = "evidence" | "execution" | "technical" | null;
type ConversationItem = {
  key: string;
  at: string;
  order: number;
  content: ReactNode;
};

const PHASE_COPY: Record<Task["status"], string> = {
  CREATED: "Task accepted and queued.",
  UNDERSTANDING: "Understanding your request and validating its scope.",
  WAITING_CLARIFICATION: "Waiting for the information needed to continue.",
  PLANNING: "Building and validating a governed execution plan.",
  EXECUTING: "Running approved analysis steps.",
  WAITING_APPROVAL: "Execution is paused for an explicit approval decision.",
  RETRYING: "A bounded retry is scheduled.",
  REPLANNING: "Revising the plan within the original authorized scope.",
  VERIFYING: "Verifying evidence, calculations, and report integrity.",
  COMPLETED: "Verification passed and the task is complete.",
  FAILED: "The task stopped safely.",
  CANCELLED: "The task was cancelled. Committed evidence is retained.",
};

function taskTitle(task: Task): string {
  return task.task_summary.length <= 72
    ? task.task_summary
    : `${task.task_summary.slice(0, 69)}…`;
}

function conversationItems(task: Task): ConversationItem[] {
  const projection = task.interaction_projection;
  const items: ConversationItem[] = [
    {
      key: "initial-user-message",
      at: projection.initial_user_message.created_at,
      order: 10,
      content: (
        <article className="message message--user">
          <div className="message__content">
            <p>{projection.initial_user_message.display_text}</p>
          </div>
        </article>
      ),
    },
  ];
  projection.phase_events.forEach((event, index) => {
    items.push({
      key: `phase-${index}-${event.phase}`,
      at: event.occurred_at,
      order: 20,
      content: (
        <div className="status-event">
          <span aria-hidden="true" />
          {PHASE_COPY[event.phase]}
        </div>
      ),
    });
  });
  projection.clarification_rounds.forEach((round) => {
    items.push({
      key: `clarification-${round.clarification_id}`,
      at: round.created_at,
      order: 30,
      content: (
        <article className="message message--agent message--clarification">
          <div className="message__avatar" aria-hidden="true">
            EC
          </div>
          <div className="message__content">
            <p className="message__author">Enterprise Knowledge Copilot</p>
            <p>I need a little more information before I can continue.</p>
            <ol className="clarification-questions">
              {round.questions.map((question) => (
                <li key={question.field}>
                  {question.prompt}
                  {question.allowed_values.length > 0 && (
                    <span className="clarification-options">
                      Authorized options: {question.allowed_values.join(", ")}
                    </span>
                  )}
                </li>
              ))}
            </ol>
          </div>
        </article>
      ),
    });
    if (round.response_display_text && round.submitted_at) {
      items.push({
        key: `clarification-response-${round.clarification_id}`,
        at: round.submitted_at,
        order: 40,
        content: (
          <article className="message message--user">
            <div className="message__content">
              <p>{round.response_display_text}</p>
            </div>
          </article>
        ),
      });
    }
  });
  projection.approval_summaries.forEach((approval) => {
    items.push({
      key: `approval-${approval.approval_id}`,
      at: approval.created_at,
      order: 50,
      content:
        approval.status === "PENDING" ? (
          <ApprovalCard
            taskId={task.task_id}
            approvalId={approval.approval_id}
          />
        ) : (
          <article className="interaction-card approval-summary-card">
            <p className="card-kicker">
              Approval {approval.status.toLowerCase()}
            </p>
            <h3>{approval.safe_label}</h3>
            <p>
              {approval.resolution_action
                ? `Decision: ${approval.resolution_action.toLowerCase()}`
                : "This approval is no longer actionable."}
            </p>
          </article>
        ),
    });
  });
  if (projection.result) {
    items.push({
      key: "task-result",
      at:
        task.completed_at ??
        task.cancelled_at ??
        projection.initial_user_message.created_at,
      order: 60,
      content: (
        <article
          className={`message message--agent message--result message--result-${projection.result.final_status.toLowerCase()}`}
        >
          <div className="message__avatar" aria-hidden="true">
            EC
          </div>
          <div className="message__content">
            <p className="message__author">Enterprise Knowledge Copilot</p>
            <p>{projection.result.safe_summary}</p>
          </div>
        </article>
      ),
    });
  }
  return items.sort(
    (a, b) =>
      a.at.localeCompare(b.at) ||
      a.order - b.order ||
      a.key.localeCompare(b.key),
  );
}

export function TaskConversationPage() {
  const { taskId = "" } = useParams();
  const task = useTask(taskId);
  const queryClient = useQueryClient();
  const cancel = useCancelTask(taskId);
  const [drawer, setDrawer] = useState<Drawer>(null);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [composerMessage, setComposerMessage] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const previousStatus = useRef<Task["status"] | undefined>(undefined);
  const pendingClarificationId =
    task.data?.pending_clarification?.clarification_id ?? "";
  const clarification = useSubmitClarification(taskId, pendingClarificationId);
  const evidence = useEvidence(taskId, drawer === "evidence");
  const steps = useSteps(taskId, task.data?.status, drawer === "execution");

  const items = useMemo(
    () => (task.data ? conversationItems(task.data) : []),
    [task.data],
  );
  useEffect(() => {
    const target = endRef.current;
    if (typeof target?.scrollIntoView === "function")
      target.scrollIntoView({ block: "end" });
  }, [items.length, task.data?.status]);
  useEffect(() => {
    const current = task.data?.status;
    refreshRelatedOnStatusChange(
      taskId,
      previousStatus.current,
      current,
      queryClient,
    );
    previousStatus.current = current;
  }, [queryClient, task.data?.status, taskId]);

  if (task.isPending)
    return (
      <div className="conversation-loading">
        <LoadingState label="Reconstructing task conversation…" />
      </div>
    );
  if (task.isError)
    return (
      <div className="conversation-loading">
        <ErrorPanel error={task.error} retry={() => void task.refetch()} />
      </div>
    );

  const detail = task.data;
  const waitingClarification =
    detail.status === "WAITING_CLARIFICATION" &&
    Boolean(pendingClarificationId);
  const composerDisabled = !waitingClarification;
  const terminal = ["COMPLETED", "FAILED", "CANCELLED"].includes(detail.status);
  const composerPlaceholder = waitingClarification
    ? "Reply with the requested information…"
    : detail.status === "WAITING_APPROVAL"
      ? "Resolve the approval card to continue"
      : terminal
        ? "Start a new task to continue"
        : "This task is currently executing.";

  async function submitClarification() {
    const message = draft.trim();
    if (!message || !pendingClarificationId) return;
    setComposerMessage(null);
    try {
      await clarification.mutateAsync({ message });
      setDraft("");
    } catch (original) {
      const error = normalizeThrownError(original);
      if (error.status === 409) {
        setComposerMessage(
          "This request already moved forward. The latest task state is loading.",
        );
        void task.refetch();
      } else {
        setComposerMessage(error.message);
      }
    }
  }

  return (
    <section
      className="conversation-workspace"
      aria-labelledby="task-conversation-title"
    >
      <header className="conversation-topbar">
        <div className="conversation-title-block">
          <p className="conversation-topbar__label">Task conversation</p>
          <h1 id="task-conversation-title">{taskTitle(detail)}</h1>
          <div className="conversation-status">
            <StatusBadge status={detail.status} />
            <span>{runtimeLabel(detail.status, detail.runtime_status)}</span>
          </div>
        </div>
        <div className="conversation-actions">
          <button
            className="text-button"
            type="button"
            onClick={() => setDrawer("evidence")}
          >
            Evidence
          </button>
          <button
            className="text-button"
            type="button"
            onClick={() => setDrawer("execution")}
          >
            Execution
          </button>
          {detail.error_summary && (
            <button
              className="text-button"
              type="button"
              onClick={() => setDrawer("technical")}
            >
              Details
            </button>
          )}
          {cancellableTaskStatuses.has(detail.status) && (
            <button
              className="text-button text-button--danger"
              type="button"
              onClick={() => setCancelOpen(true)}
            >
              Cancel task
            </button>
          )}
        </div>
      </header>

      <div className="conversation-scroll">
        <div className="conversation-stream">
          {items.map((item) => (
            <div key={item.key}>{item.content}</div>
          ))}
          <ArtifactCards
            taskId={taskId}
            enabled={detail.status === "COMPLETED"}
            openEvidence={() => setDrawer("evidence")}
          />
          <div ref={endRef} />
        </div>
      </div>

      <div className="composer-dock">
        <TaskComposer
          value={draft}
          onChange={setDraft}
          onSubmit={submitClarification}
          pending={clarification.isPending}
          disabled={composerDisabled}
          placeholder={composerPlaceholder}
          helperText={
            waitingClarification
              ? "Reply in natural language · Enter to send · Shift+Enter for a new line"
              : composerPlaceholder
          }
          error={composerMessage}
        />
        {terminal && (
          <Link className="composer-new-task-link" to="/">
            Start a new task
          </Link>
        )}
        <p className="governance-note">
          Messages never authorize tools or broaden your current data scope.
        </p>
      </div>

      <ConfirmDialog
        open={cancelOpen}
        title="Cancel this task?"
        message="Execution will stop at a safe boundary. Already committed evidence is retained."
        confirmLabel="Cancel task"
        busy={cancel.isPending}
        destructive
        onClose={() => setCancelOpen(false)}
        onConfirm={() => {
          void cancel
            .mutateAsync()
            .then(() => setCancelOpen(false))
            .catch(() => undefined);
        }}
      />

      <DetailDrawer
        title="Evidence"
        open={drawer === "evidence"}
        onClose={() => setDrawer(null)}
      >
        {evidence.isPending && (
          <LoadingState label="Loading minimized evidence…" />
        )}
        {evidence.isError && (
          <ErrorPanel
            error={evidence.error}
            retry={() => void evidence.refetch()}
          />
        )}
        {evidence.data?.evidence.length === 0 && (
          <p>No Evidence has been committed yet.</p>
        )}
        <div className="drawer-list">
          {evidence.data?.evidence.map((item) => (
            <article className="drawer-card" key={item.evidence_id}>
              <p className="card-kicker">{item.type}</p>
              <h3>{item.content_summary}</h3>
              <dl>
                <div>
                  <dt>Source</dt>
                  <dd>{item.source}</dd>
                </div>
                <div>
                  <dt>Produced by</dt>
                  <dd>{item.produced_by}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      </DetailDrawer>

      <DetailDrawer
        title="Execution details"
        open={drawer === "execution"}
        onClose={() => setDrawer(null)}
      >
        <p className="drawer-intro">
          Advanced step records are read-only. Policy, approval, evidence, and
          audit authority remain on the server.
        </p>
        {steps.isPending && <LoadingState label="Loading execution records…" />}
        {steps.isError && (
          <ErrorPanel error={steps.error} retry={() => void steps.refetch()} />
        )}
        {steps.data?.steps.length === 0 && (
          <p>No plan steps are available yet.</p>
        )}
        <ol className="execution-list">
          {steps.data?.steps.map((step) => (
            <li key={step.step_id}>
              <div>
                <strong>{step.purpose}</strong>
                <span>{step.status}</span>
              </div>
              <p>
                {step.tool_name} · {step.attempt_count} attempt
                {step.attempt_count === 1 ? "" : "s"} ·{" "}
                {formatDuration(step.latency_ms)}
              </p>
            </li>
          ))}
        </ol>
      </DetailDrawer>

      <DetailDrawer
        title="Technical details"
        open={drawer === "technical"}
        onClose={() => setDrawer(null)}
      >
        <dl className="technical-list">
          <div>
            <dt>Task ID</dt>
            <dd>{detail.task_id}</dd>
          </div>
          <div>
            <dt>Trace ID</dt>
            <dd>{detail.trace_id}</dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd>{detail.status}</dd>
          </div>
          <div>
            <dt>Last update</dt>
            <dd>{formatDate(detail.completed_at ?? detail.started_at)}</dd>
          </div>
          {detail.error_summary && (
            <div>
              <dt>Safe error</dt>
              <dd>{detail.error_summary}</dd>
            </div>
          )}
        </dl>
      </DetailDrawer>
    </section>
  );
}
