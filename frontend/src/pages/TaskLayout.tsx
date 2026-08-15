import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useParams } from "react-router-dom";

import type { Task, TaskStatus } from "../api/types";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { MetadataList } from "../components/MetadataList";
import { ErrorPanel, LoadingState } from "../components/PageState";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import {
  refreshRelatedOnStatusChange,
  useCancelTask,
  useTask,
} from "../features/tasks/queries";
import { formatDate } from "../utils/format";
import { cancellableTaskStatuses } from "../utils/status";

export interface TaskOutletContext {
  task: Task;
  taskId: string;
}

export function TaskLayout() {
  const { taskId = "" } = useParams();
  const taskQuery = useTask(taskId);
  const cancel = useCancelTask(taskId);
  const queryClient = useQueryClient();
  const previousStatus = useRef<TaskStatus | undefined>(undefined);
  const [confirmCancel, setConfirmCancel] = useState(false);

  useEffect(() => {
    const current = taskQuery.data?.status;
    refreshRelatedOnStatusChange(
      taskId,
      previousStatus.current,
      current,
      queryClient,
    );
    previousStatus.current = current;
  }, [queryClient, taskId, taskQuery.data?.status]);

  if (taskQuery.isPending)
    return <LoadingState label="Loading governed task…" />;
  if (taskQuery.isError)
    return (
      <ErrorPanel
        error={taskQuery.error}
        retry={() => void taskQuery.refetch()}
      />
    );

  const task = taskQuery.data;
  const canCancel = cancellableTaskStatuses.has(task.status);
  const tabs = [
    {
      to: `/tasks/${encodeURIComponent(taskId)}`,
      label: "Overview",
      end: true,
    },
    {
      to: `/tasks/${encodeURIComponent(taskId)}/evidence`,
      label: `Evidence (${task.evidence_count})`,
      end: false,
    },
    {
      to: `/tasks/${encodeURIComponent(taskId)}/report`,
      label: `Report (${task.artifact_count})`,
      end: false,
    },
  ];

  return (
    <>
      <PageHeader
        eyebrow="Governed task"
        title={task.task_summary || task.task_id}
        description="Authoritative lifecycle, execution, Evidence, approval, and Artifact state."
        actions={
          <div className="button-row">
            <StatusBadge status={task.status} />
            {canCancel && (
              <button
                className="button button--secondary"
                type="button"
                onClick={() => setConfirmCancel(true)}
              >
                Cancel task
              </button>
            )}
          </div>
        }
      />
      <section className="panel task-identity-panel">
        <MetadataList
          compact
          items={[
            { label: "Task ID", value: task.task_id },
            { label: "Trace ID", value: task.trace_id },
            {
              label: "Current step",
              value: task.current_step ?? "No active step",
            },
            { label: "Created", value: formatDate(task.created_at) },
            {
              label: "Completed",
              value: formatDate(task.completed_at ?? task.cancelled_at),
            },
          ]}
        />
      </section>
      {task.pending_approval_id && (
        <section className="approval-banner" role="status">
          <div>
            <p className="eyebrow">Human decision required</p>
            <h2>Execution is waiting for governed approval</h2>
            <p>
              The target action is frozen. Only an authorized approver can
              inspect and resolve it.
            </p>
          </div>
          <Link
            className="button button--warning"
            to={`/tasks/${encodeURIComponent(taskId)}/approvals/${encodeURIComponent(task.pending_approval_id)}`}
          >
            Open approval workbench
          </Link>
        </section>
      )}
      {task.error_summary && (
        <section className="task-error-summary" role="alert">
          <strong>Task failure</strong>
          <p>{task.error_summary}</p>
        </section>
      )}
      <nav className="task-tabs" aria-label="Task detail sections">
        {tabs.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.end}
            className={({ isActive }) =>
              isActive ? "task-tab task-tab--active" : "task-tab"
            }
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <Outlet context={{ task, taskId } satisfies TaskOutletContext} />
      {cancel.isError && (
        <ErrorPanel error={cancel.error} title="Task could not be cancelled" />
      )}
      <ConfirmDialog
        open={confirmCancel}
        title="Cancel this task?"
        message="The system will stop scheduling new work, revoke pending approval, retain committed Evidence, and transition the task according to the frozen state machine."
        confirmLabel="Cancel task"
        destructive
        busy={cancel.isPending}
        onClose={() => setConfirmCancel(false)}
        onConfirm={() =>
          void cancel
            .mutateAsync()
            .then(() => setConfirmCancel(false))
            .catch(() => undefined)
        }
      />
    </>
  );
}
