import type { components } from "./generated/schema";

export type ApprovalDetail = components["schemas"]["ApprovalDetailResponse"];
export type ApprovalResolution =
  components["schemas"]["ApprovalResolutionResponse"];
export type ApprovalResolutionRequest =
  components["schemas"]["ApprovalResolutionRequest"];
export type ClarificationDetail =
  components["schemas"]["ClarificationDetailResponse"];
export type ClarificationInputType =
  components["schemas"]["ClarificationInputType"];
export type ClarificationSubmission =
  components["schemas"]["ClarificationSubmissionResponse"];
export type ClarificationSubmissionRequest =
  components["schemas"]["ClarificationSubmissionRequest"];
export type Artifact = components["schemas"]["ArtifactMetadataResponse"];
export type ArtifactList = components["schemas"]["ArtifactListResponse"];
export type Evidence = components["schemas"]["TaskEvidenceResponse"];
export type EvidenceList = components["schemas"]["TaskEvidenceListResponse"];
export type EvidenceType = components["schemas"]["EvidenceType"];
export type Health = components["schemas"]["HealthResponse"];
export type JsonValue = components["schemas"]["JsonValue"];
export type Liveness = components["schemas"]["LivenessResponse"];
export type Readiness = components["schemas"]["ReadinessResponse"];
export type RuntimeStatus = components["schemas"]["RuntimeStatus"];
export type Step = components["schemas"]["TaskStepResponse"];
export type StepList = components["schemas"]["TaskStepsResponse"];
export type StepStatus = components["schemas"]["PublicStepStatus"];
export type Task = components["schemas"]["TaskDetailResponse"];
export type TaskSummary = components["schemas"]["TaskResponse"];
export type TaskListItem = components["schemas"]["TaskListItemResponse"];
export type TaskCreateRequest =
  components["schemas"]["NaturalLanguageTaskSubmission"];
export type TaskCreateResponse =
  components["schemas"]["TaskSubmissionResponse"];
export type TaskErrorPayload = components["schemas"]["TaskErrorResponse"];
export type TaskList = components["schemas"]["TaskListResponse"];
export type TaskStatus = components["schemas"]["TaskStatus"];
export type TaskType = components["schemas"]["TaskType"];
