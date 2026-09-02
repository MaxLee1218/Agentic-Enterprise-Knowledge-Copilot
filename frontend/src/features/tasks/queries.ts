import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import type {
  ApprovalResolutionRequest,
  ClarificationSubmissionRequest,
  Task,
  TaskCreateRequest,
  TaskStatus,
} from "../../api/types";
import { pollingInterval } from "../../utils/status";
import {
  cancelTask,
  createTask,
  getApproval,
  getArtifacts,
  getClarification,
  getEvidence,
  getHealth,
  getLiveness,
  getReadiness,
  getSteps,
  getTask,
  listTasks,
  resolveApproval,
  submitClarification,
  type TaskListParams,
} from "./api";

export const queryKeys = {
  tasks: (params: TaskListParams) => ["tasks", params] as const,
  taskCollections: ["tasks"] as const,
  task: (taskId: string) => ["task", taskId] as const,
  steps: (taskId: string) => ["steps", taskId] as const,
  evidence: (taskId: string) => ["evidence", taskId] as const,
  artifacts: (taskId: string) => ["artifacts", taskId] as const,
  approval: (taskId: string, approvalId: string) =>
    ["approval", taskId, approvalId] as const,
  clarification: (taskId: string, clarificationId: string) =>
    ["clarification", taskId, clarificationId] as const,
  health: ["health"] as const,
};

export function useTaskList(params: TaskListParams) {
  return useQuery({
    queryKey: queryKeys.tasks(params),
    queryFn: () => listTasks(params),
  });
}

export function useTask(taskId: string): UseQueryResult<Task> {
  return useQuery({
    queryKey: queryKeys.task(taskId),
    queryFn: () => getTask(taskId),
    enabled: Boolean(taskId),
    refetchInterval: (query) => pollingInterval(query.state.data?.status),
  });
}

export function useSteps(taskId: string, status?: TaskStatus) {
  return useQuery({
    queryKey: queryKeys.steps(taskId),
    queryFn: () => getSteps(taskId),
    enabled: Boolean(taskId),
    refetchInterval: pollingInterval(status),
  });
}

export function useEvidence(taskId: string) {
  return useQuery({
    queryKey: queryKeys.evidence(taskId),
    queryFn: () => getEvidence(taskId),
    enabled: Boolean(taskId),
  });
}

export function useArtifacts(taskId: string) {
  return useQuery({
    queryKey: queryKeys.artifacts(taskId),
    queryFn: () => getArtifacts(taskId),
    enabled: Boolean(taskId),
  });
}

export function useApproval(taskId: string, approvalId: string) {
  return useQuery({
    queryKey: queryKeys.approval(taskId, approvalId),
    queryFn: () => getApproval(taskId, approvalId),
    enabled: Boolean(taskId && approvalId),
  });
}

export function useClarification(taskId: string, clarificationId: string) {
  return useQuery({
    queryKey: queryKeys.clarification(taskId, clarificationId),
    queryFn: () => getClarification(taskId, clarificationId),
    enabled: Boolean(taskId && clarificationId),
  });
}

function invalidateTaskQueries(
  queryClient: ReturnType<typeof useQueryClient>,
  taskId: string,
) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.task(taskId) });
  void queryClient.invalidateQueries({ queryKey: queryKeys.steps(taskId) });
  void queryClient.invalidateQueries({ queryKey: queryKeys.evidence(taskId) });
  void queryClient.invalidateQueries({ queryKey: queryKeys.artifacts(taskId) });
  void queryClient.invalidateQueries({ queryKey: queryKeys.taskCollections });
}

export function useCreateTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: TaskCreateRequest) => createTask(input),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.taskCollections }),
  });
}

export function useCancelTask(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => cancelTask(taskId),
    onSuccess: () => invalidateTaskQueries(queryClient, taskId),
  });
}

export function useResolveApproval(taskId: string, approvalId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: ApprovalResolutionRequest) =>
      resolveApproval(taskId, approvalId, input),
    onSuccess: () => {
      invalidateTaskQueries(queryClient, taskId);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.approval(taskId, approvalId),
      });
    },
  });
}

export function useSubmitClarification(
  taskId: string,
  clarificationId: string,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: ClarificationSubmissionRequest) =>
      submitClarification(taskId, clarificationId, input),
    onSuccess: () => {
      invalidateTaskQueries(queryClient, taskId);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.clarification(taskId, clarificationId),
      });
    },
  });
}

export function useSystemHealth() {
  return {
    process: useQuery({
      queryKey: [...queryKeys.health, "process"],
      queryFn: getHealth,
    }),
    live: useQuery({
      queryKey: [...queryKeys.health, "live"],
      queryFn: getLiveness,
    }),
    ready: useQuery({
      queryKey: [...queryKeys.health, "ready"],
      queryFn: getReadiness,
      refetchInterval: 15_000,
    }),
  };
}

export function refreshRelatedOnStatusChange(
  taskId: string,
  previous: TaskStatus | undefined,
  current: TaskStatus | undefined,
  queryClient: ReturnType<typeof useQueryClient>,
) {
  if (!previous || !current || previous === current) return;
  void queryClient.invalidateQueries({ queryKey: queryKeys.steps(taskId) });
  void queryClient.invalidateQueries({ queryKey: queryKeys.evidence(taskId) });
  void queryClient.invalidateQueries({ queryKey: queryKeys.artifacts(taskId) });
}
