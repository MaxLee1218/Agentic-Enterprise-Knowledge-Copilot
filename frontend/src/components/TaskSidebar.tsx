import { NavLink } from "react-router-dom";

import type { TaskListItem } from "../api/types";
import { useTaskHistory } from "../features/tasks/queries";
import { runtimeLabel, statusTone } from "../utils/status";

const GROUPS = [
  "Today",
  "Yesterday",
  "Previous 7 days",
  "Previous 30 days",
  "Older",
] as const;
type GroupName = (typeof GROUPS)[number];

function dayStart(value: Date): number {
  return new Date(
    value.getFullYear(),
    value.getMonth(),
    value.getDate(),
  ).getTime();
}

function groupName(createdAt: string): GroupName {
  const age = Math.floor(
    (dayStart(new Date()) - dayStart(new Date(createdAt))) / 86_400_000,
  );
  if (age <= 0) return "Today";
  if (age === 1) return "Yesterday";
  if (age <= 7) return "Previous 7 days";
  if (age <= 30) return "Previous 30 days";
  return "Older";
}

function groupedTasks(tasks: TaskListItem[]): Map<GroupName, TaskListItem[]> {
  const result = new Map<GroupName, TaskListItem[]>();
  for (const task of tasks) {
    const group = groupName(task.created_at);
    result.set(group, [...(result.get(group) ?? []), task]);
  }
  return result;
}

export function TaskSidebar({
  collapsed,
  mobile,
  closeMobile,
  toggleCollapsed,
}: {
  collapsed: boolean;
  mobile: boolean;
  closeMobile: () => void;
  toggleCollapsed: () => void;
}) {
  const history = useTaskHistory();
  const tasks = history.data?.pages.flatMap((page) => page.items) ?? [];
  const groups = groupedTasks(tasks);

  return (
    <aside
      className={`task-sidebar ${collapsed ? "task-sidebar--collapsed" : ""} ${mobile ? "task-sidebar--mobile-open" : ""}`}
      aria-label="Task history"
    >
      <div className="task-sidebar__top">
        <NavLink
          className="workspace-brand"
          to="/"
          onClick={closeMobile}
          aria-label="Enterprise Knowledge Copilot"
        >
          <span className="workspace-brand__mark" aria-hidden="true">
            EC
          </span>
          {!collapsed && <span>Knowledge Copilot</span>}
        </NavLink>
        <button
          className="icon-button task-sidebar__collapse"
          type="button"
          onClick={toggleCollapsed}
          aria-label={
            collapsed ? "Expand task history" : "Collapse task history"
          }
          title={collapsed ? "Expand task history" : "Collapse task history"}
        >
          <span aria-hidden="true">{collapsed ? "›" : "‹"}</span>
        </button>
      </div>

      <NavLink className="new-task-button" to="/" onClick={closeMobile}>
        <span aria-hidden="true">＋</span>
        {!collapsed && <span>New Task</span>}
      </NavLink>

      {!collapsed && (
        <div className="task-history" aria-live="polite">
          {history.isPending && (
            <p className="sidebar-note">Loading task history…</p>
          )}
          {history.isError && (
            <button
              className="sidebar-retry"
              type="button"
              onClick={() => void history.refetch()}
            >
              Task history unavailable · Retry
            </button>
          )}
          {!history.isPending && !history.isError && tasks.length === 0 && (
            <p className="sidebar-note">
              No tasks yet. Start with a business question.
            </p>
          )}
          {GROUPS.map((group) => {
            const items = groups.get(group);
            if (!items?.length) return null;
            return (
              <section
                className="task-group"
                key={group}
                aria-labelledby={`task-group-${group.replaceAll(" ", "-")}`}
              >
                <h2 id={`task-group-${group.replaceAll(" ", "-")}`}>{group}</h2>
                <ul>
                  {items.map((task) => (
                    <li key={task.task_id}>
                      <NavLink
                        className={({ isActive }) =>
                          `task-history-item ${isActive ? "task-history-item--active" : ""}`
                        }
                        to={`/tasks/${encodeURIComponent(task.task_id)}`}
                        onClick={closeMobile}
                      >
                        <span className="task-history-item__title">
                          {task.task_summary}
                        </span>
                        <span className="task-history-item__meta">
                          <span
                            className={`status-dot status-dot--${statusTone(task.status)}`}
                            aria-hidden="true"
                          />
                          {runtimeLabel(task.status, task.runtime_status)}
                        </span>
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
          {history.hasNextPage && (
            <button
              className="sidebar-load-more"
              type="button"
              disabled={history.isFetchingNextPage}
              onClick={() => void history.fetchNextPage()}
            >
              {history.isFetchingNextPage ? "Loading…" : "Load older tasks"}
            </button>
          )}
        </div>
      )}

      <NavLink className="system-link" to="/system" onClick={closeMobile}>
        <span aria-hidden="true">◌</span>
        {!collapsed && <span>System</span>}
      </NavLink>
    </aside>
  );
}
