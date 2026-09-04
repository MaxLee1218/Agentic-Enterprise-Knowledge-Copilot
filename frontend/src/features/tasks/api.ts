import { apiClient, unwrap } from "../../api/client";
import type {
  ApprovalDetail,
  ApprovalResolution,
  ApprovalResolutionRequest,
  ArtifactList,
  ClarificationDetail,
  ClarificationSubmission,
  ClarificationSubmissionRequest,
  EvidenceList,
  Health,
  Liveness,
  Readiness,
  StepList,
  Task,
  TaskCreateRequest,
  TaskCreateResponse,
  TaskList,
  TaskSummary,
  TaskStatus,
} from "../../api/types";

export interface TaskListParams {
  status?: TaskStatus;
  limit: number;
  offset: number;
}

export async function listTasks(params: TaskListParams): Promise<TaskList> {
  return unwrap(
    await apiClient.GET("/v1/tasks", {
      params: { query: params },
    }),
  );
}

export async function createTask(
  input: TaskCreateRequest,
  idempotencyKey?: string,
): Promise<TaskCreateResponse> {
  return unwrap(
    await apiClient.POST("/v1/tasks", {
      body: input,
      params: { header: { "Idempotency-Key": idempotencyKey } },
    }),
  );
}

export async function getTask(taskId: string): Promise<Task> {
  return unwrap(
    await apiClient.GET("/v1/tasks/{task_id}", {
      params: { path: { task_id: taskId } },
    }),
  );
}

export async function getSteps(taskId: string): Promise<StepList> {
  return unwrap(
    await apiClient.GET("/v1/tasks/{task_id}/steps", {
      params: { path: { task_id: taskId } },
    }),
  );
}

export async function getEvidence(taskId: string): Promise<EvidenceList> {
  return unwrap(
    await apiClient.GET("/v1/tasks/{task_id}/evidence", {
      params: { path: { task_id: taskId } },
    }),
  );
}

export async function getArtifacts(taskId: string): Promise<ArtifactList> {
  return unwrap(
    await apiClient.GET("/v1/tasks/{task_id}/artifacts", {
      params: { path: { task_id: taskId } },
    }),
  );
}

export async function cancelTask(taskId: string): Promise<TaskSummary> {
  return unwrap(
    await apiClient.POST("/v1/tasks/{task_id}/cancel", {
      params: { path: { task_id: taskId } },
    }),
  );
}

export async function getApproval(
  taskId: string,
  approvalId: string,
): Promise<ApprovalDetail> {
  return unwrap(
    await apiClient.GET("/v1/tasks/{task_id}/approvals/{approval_id}", {
      params: { path: { task_id: taskId, approval_id: approvalId } },
    }),
  );
}

export async function getClarification(
  taskId: string,
  clarificationId: string,
): Promise<ClarificationDetail> {
  return unwrap(
    await apiClient.GET(
      "/v1/tasks/{task_id}/clarifications/{clarification_id}",
      {
        params: {
          path: { task_id: taskId, clarification_id: clarificationId },
        },
      },
    ),
  );
}

export async function submitClarification(
  taskId: string,
  clarificationId: string,
  input: ClarificationSubmissionRequest,
): Promise<ClarificationSubmission> {
  return unwrap(
    await apiClient.POST(
      "/v1/tasks/{task_id}/clarifications/{clarification_id}",
      {
        params: {
          path: { task_id: taskId, clarification_id: clarificationId },
        },
        body: input,
      },
    ),
  );
}

export async function resolveApproval(
  taskId: string,
  approvalId: string,
  input: ApprovalResolutionRequest,
): Promise<ApprovalResolution> {
  return unwrap(
    await apiClient.POST("/v1/tasks/{task_id}/approvals/{approval_id}", {
      params: { path: { task_id: taskId, approval_id: approvalId } },
      body: input,
    }),
  );
}

export async function getHealth(): Promise<Health> {
  return unwrap(await apiClient.GET("/health"));
}

export async function getLiveness(): Promise<Liveness> {
  return unwrap(await apiClient.GET("/health/live"));
}

function isReadiness(value: unknown): value is Readiness {
  if (typeof value !== "object" || value === null) return false;
  return (
    "status" in value && "accepts_tasks" in value && "dependencies" in value
  );
}

export async function getReadiness(): Promise<Readiness> {
  const result = await apiClient.GET("/health/ready");
  if (result.data !== undefined) return result.data;
  if (result.response.status === 503 && isReadiness(result.error))
    return result.error;
  return unwrap<Readiness>({
    data: result.data,
    error: result.error,
    response: result.response,
  });
}

export function artifactDownloadUrl(
  taskId: string,
  artifactId: string,
): string {
  return `/api/v1/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactId)}`;
}
