import { Link, useSearchParams } from "react-router-dom";

import type { TaskStatus } from "../api/types";
import { EmptyState, ErrorPanel, LoadingState } from "../components/PageState";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";
import { TaskTypeBadge } from "../components/TaskTypeBadge";
import { useTaskList } from "../features/tasks/queries";
import { formatDate, shorten } from "../utils/format";
import { taskStatuses } from "../utils/status";

const pageSize = 20;

function validStatus(value: string | null): TaskStatus | undefined {
  return taskStatuses.includes(value as TaskStatus)
    ? (value as TaskStatus)
    : undefined;
}

function validOffset(value: string | null): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : 0;
}

export function TaskListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const status = validStatus(searchParams.get("status"));
  const offset = validOffset(searchParams.get("offset"));
  const tasks = useTaskList({ status, limit: pageSize, offset });

  function updateStatus(value: string) {
    const next = new URLSearchParams();
    if (value) next.set("status", value);
    setSearchParams(next);
  }

  function move(nextOffset: number) {
    const next = new URLSearchParams(searchParams);
    next.set("offset", String(nextOffset));
    setSearchParams(next);
  }

  return (
    <>
      <PageHeader
        eyebrow="Governed task history"
        title="Tasks"
        description="Review task state, execution evidence, approvals, and published deliverables."
        actions={
          <Link className="button" to="/tasks/new">
            Run enterprise task
          </Link>
        }
      />
      <section className="toolbar" aria-label="Task history filters">
        <label>
          Status
          <select
            value={status ?? ""}
            onChange={(event) => updateStatus(event.target.value)}
          >
            <option value="">All statuses</option>
            {taskStatuses.map((item) => (
              <option key={item} value={item}>
                {item.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <span className="toolbar__note">
          Newest first · current authenticated owner
        </span>
      </section>

      {tasks.isPending && <LoadingState label="Loading task history…" />}
      {tasks.isError && (
        <ErrorPanel error={tasks.error} retry={() => void tasks.refetch()} />
      )}
      {tasks.data && tasks.data.items.length === 0 && (
        <EmptyState
          title="No tasks found"
          message="Run a bounded enterprise analysis task or change the status filter."
        />
      )}
      {tasks.data && tasks.data.items.length > 0 && (
        <section
          className="panel table-panel"
          aria-labelledby="task-history-title"
        >
          <div className="panel-heading">
            <h2 id="task-history-title">Task history</h2>
            <span>{tasks.data.total} total</span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Task</th>
                  <th>Request summary</th>
                  <th>Use case</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Artifacts</th>
                </tr>
              </thead>
              <tbody>
                {tasks.data.items.map((task) => (
                  <tr key={task.task_id}>
                    <td>
                      <Link
                        className="mono-link"
                        to={`/tasks/${encodeURIComponent(task.task_id)}`}
                      >
                        {shorten(task.task_id, 22)}
                      </Link>
                    </td>
                    <td className="task-summary-cell">{task.task_summary}</td>
                    <td>
                      <TaskTypeBadge taskType={task.task_type} />
                    </td>
                    <td>
                      <StatusBadge status={task.status} />
                    </td>
                    <td>{formatDate(task.created_at)}</td>
                    <td>{task.artifact_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pagination" aria-label="Task history pagination">
            <button
              className="button button--secondary"
              type="button"
              disabled={offset === 0}
              onClick={() => move(Math.max(0, offset - pageSize))}
            >
              Previous
            </button>
            <span>
              Showing {offset + 1}–
              {Math.min(offset + pageSize, tasks.data.total)} of{" "}
              {tasks.data.total}
            </span>
            <button
              className="button button--secondary"
              type="button"
              disabled={offset + pageSize >= tasks.data.total}
              onClick={() => move(offset + pageSize)}
            >
              Next
            </button>
          </div>
        </section>
      )}
    </>
  );
}
