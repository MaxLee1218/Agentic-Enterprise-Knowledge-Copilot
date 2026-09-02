import type { StepStatus, TaskStatus } from "../api/types";
import { statusLabel, statusTone } from "../utils/status";

interface StatusBadgeProps {
  status: TaskStatus | StepStatus;
}

export function StatusBadge({ status }: StatusBadgeProps) {
  return (
    <span className={`status-badge status-badge--${statusTone(status)}`}>
      <span className="status-badge__mark" aria-hidden="true" />
      {statusLabel(status)}
    </span>
  );
}
