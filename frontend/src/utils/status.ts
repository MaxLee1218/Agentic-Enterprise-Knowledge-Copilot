import type { RuntimeStatus, StepStatus, TaskStatus } from "../api/types";

export const taskStatuses: readonly TaskStatus[] = [
  "CREATED",
  "UNDERSTANDING",
  "WAITING_CLARIFICATION",
  "PLANNING",
  "EXECUTING",
  "WAITING_APPROVAL",
  "RETRYING",
  "REPLANNING",
  "VERIFYING",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
];

export const terminalTaskStatuses = new Set<TaskStatus>([
  "COMPLETED",
  "FAILED",
  "CANCELLED",
]);

export const cancellableTaskStatuses = new Set<TaskStatus>([
  "CREATED",
  "UNDERSTANDING",
  "WAITING_CLARIFICATION",
  "PLANNING",
  "EXECUTING",
  "WAITING_APPROVAL",
  "RETRYING",
  "REPLANNING",
  "VERIFYING",
]);

export type StatusTone =
  | "neutral"
  | "active"
  | "warning"
  | "success"
  | "danger";

const taskStatusTones: Record<TaskStatus, StatusTone> = {
  CREATED: "neutral",
  UNDERSTANDING: "active",
  WAITING_CLARIFICATION: "warning",
  PLANNING: "active",
  EXECUTING: "active",
  WAITING_APPROVAL: "warning",
  RETRYING: "warning",
  REPLANNING: "warning",
  VERIFYING: "active",
  COMPLETED: "success",
  FAILED: "danger",
  CANCELLED: "danger",
};

const stepStatusTones: Record<StepStatus, StatusTone> = {
  PENDING: "neutral",
  SUCCESS: "success",
  BUSINESS_FAILURE: "danger",
  TECHNICAL_FAILURE: "danger",
  TIMEOUT: "danger",
  PERMISSION_DENIED: "danger",
  CANCELLED: "danger",
};

export function statusTone(status: TaskStatus | StepStatus): StatusTone {
  return status in taskStatusTones
    ? taskStatusTones[status as TaskStatus]
    : stepStatusTones[status as StepStatus];
}

export function statusLabel(status: TaskStatus | StepStatus): string {
  if (status === "WAITING_CLARIFICATION") return "Waiting for information";
  return status.replaceAll("_", " ");
}

export function pollingInterval(
  status: TaskStatus | undefined,
): number | false {
  if (!status || terminalTaskStatuses.has(status)) return false;
  return status === "WAITING_APPROVAL" || status === "WAITING_CLARIFICATION"
    ? 10_000
    : 2_000;
}

export function runtimeLabel(
  taskStatus: TaskStatus,
  runtimeStatus: RuntimeStatus,
): string {
  if (taskStatus === "WAITING_APPROVAL") return "Waiting approval";
  if (taskStatus === "WAITING_CLARIFICATION") return "Waiting for information";
  if (taskStatus === "COMPLETED") return "Completed";
  if (taskStatus === "FAILED") return "Failed";
  if (taskStatus === "CANCELLED") return "Cancelled";
  if (runtimeStatus === "READY") return "Queued";
  if (runtimeStatus === "LEASED") return "Running";
  if (runtimeStatus === "WAITING_RETRY") return "Retry scheduled";
  if (runtimeStatus === "SUSPENDED") return "Suspended";
  return "Finalizing";
}
