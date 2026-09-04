import { http, HttpResponse } from "msw";

import { approval, artifacts, evidence, steps, task } from "./fixtures";

export const baseHandlers = [
  http.get("*/api/v1/tasks", () =>
    HttpResponse.json({
      items: [
        {
          task_id: task.task_id,
          task_summary: task.task_summary,
          status: task.status,
          runtime_status: task.runtime_status,
          task_type: task.task_type,
          created_at: task.created_at,
        },
      ],
      total: 1,
      limit: 40,
      offset: 0,
    }),
  ),
  http.get("*/api/v1/tasks/:taskId", () => HttpResponse.json(task)),
  http.get("*/api/v1/tasks/:taskId/steps", () =>
    HttpResponse.json({ task_id: task.task_id, steps }),
  ),
  http.get("*/api/v1/tasks/:taskId/evidence", () =>
    HttpResponse.json({ task_id: task.task_id, evidence }),
  ),
  http.get("*/api/v1/tasks/:taskId/artifacts", () =>
    HttpResponse.json({ task_id: task.task_id, artifacts }),
  ),
  http.get("*/api/v1/tasks/:taskId/approvals/:approvalId", () =>
    HttpResponse.json(approval),
  ),
];
