import createClient from "openapi-fetch";

import type { paths } from "./generated/schema";

export const apiClient = createClient<paths>({
  baseUrl: new URL("/api", window.location.origin).toString(),
  fetch: (request: Request) => globalThis.fetch(request),
  headers: { Accept: "application/json" },
});

interface ApiResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

interface NormalizedError {
  error_code?: string;
  message?: string;
  task_id?: string | null;
  trace_id?: string;
  details?: Record<string, unknown>;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly taskId: string | null;
  readonly traceId: string | null;
  readonly details: Record<string, unknown>;

  constructor(
    message: string,
    options: {
      status?: number;
      code?: string;
      taskId?: string | null;
      traceId?: string | null;
      details?: Record<string, unknown>;
    } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status ?? 0;
    this.code = options.code ?? "NETWORK_ERROR";
    this.taskId = options.taskId ?? null;
    this.traceId = options.traceId ?? null;
    this.details = options.details ?? {};
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizePayload(value: unknown): NormalizedError {
  if (!isRecord(value)) return {};
  const detail = isRecord(value.detail) ? value.detail : value;
  return {
    error_code:
      typeof detail.error_code === "string" ? detail.error_code : undefined,
    message: typeof detail.message === "string" ? detail.message : undefined,
    task_id:
      typeof detail.task_id === "string" || detail.task_id === null
        ? detail.task_id
        : undefined,
    trace_id: typeof detail.trace_id === "string" ? detail.trace_id : undefined,
    details: isRecord(detail.details) ? detail.details : undefined,
  };
}

function defaultMessage(status: number): string {
  const messages: Record<number, string> = {
    400: "The request is not valid for the current task state.",
    401: "Authentication is required.",
    403: "You are not authorized to perform this action.",
    404: "The requested task resource was not found.",
    409: "This resource was changed or resolved by another action.",
    410: "The requested Artifact is no longer available.",
    422: "Please correct the highlighted request details.",
    500: "The task could not be processed.",
    503: "The system is temporarily not ready.",
    504: "The operation timed out.",
  };
  return messages[status] ?? `The request failed (${status}).`;
}

export function unwrap<T>(result: ApiResult<T>): T {
  if (result.data !== undefined) return result.data;
  const payload = normalizePayload(result.error);
  throw new ApiError(
    payload.message ?? defaultMessage(result.response.status),
    {
      status: result.response.status,
      code: payload.error_code,
      taskId: payload.task_id,
      traceId: payload.trace_id,
      details: payload.details,
    },
  );
}

export function normalizeThrownError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if (error instanceof DOMException && error.name === "AbortError") {
    return new ApiError("The request timed out.", { code: "REQUEST_TIMEOUT" });
  }
  if (error instanceof Error) {
    return new ApiError("The server could not be reached.", {
      code: "NETWORK_ERROR",
      details: { cause: error.name },
    });
  }
  return new ApiError("An unexpected client error occurred.", {
    code: "CLIENT_ERROR",
  });
}
