import type { StepStatus, TaskStatus } from "../api/types";
import { statusLabel, statusTone } from "../utils/status";

interface StatusBadgeProps {
  status: TaskStatus | StepStatus;
  label?: string;
}

export function StatusBadge({ status, label }: StatusBadgeProps) {
  return (
    <span
      className={`status-badge status-badge--${statusTone(status)}`}
      role="status"
      aria-live="polite"
    >
      <span className="status-badge__mark" aria-hidden="true" />
      {label ?? statusLabel(status)}
    </span>
  );
}
