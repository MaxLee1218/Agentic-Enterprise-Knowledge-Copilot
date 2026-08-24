import type { TaskType } from "../api/types";

const taskTypeLabels: Record<TaskType, string> = {
  "supplier_quality_analysis.v1": "Supplier Quality",
  "accounts_payable_analysis.v1": "Accounts Payable",
};

export function TaskTypeBadge({ taskType }: { taskType: TaskType | null }) {
  if (taskType === null)
    return <span className="task-type-badge">Unclassified</span>;
  return (
    <span
      className={`task-type-badge task-type-badge--${taskType.split("_")[0]}`}
    >
      {taskTypeLabels[taskType]}
    </span>
  );
}
