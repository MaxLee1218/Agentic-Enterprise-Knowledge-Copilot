# HTTP API

## 提交自然语言任务

`POST /v1/tasks` 的唯一必需字段是 `task`。合法任务原子持久化 Task 与初始 dispatch 后统一返回
`202 Accepted`；响应包含 `task_id`、`trace_id`、`task_status=CREATED`、
`runtime_status=READY`、`accepted_at`、`status_url` 和 `artifacts_url`。HTTP 请求不运行 Graph、
Tool、验证或报告生成。客户端通过 `status_url` 轮询。

```bash
curl -X POST http://127.0.0.1:8000/v1/tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "task": "Analyze Q2 2026 supplier quality and generate a JSON report.",
    "require_approval": true
  }'
```

## 读取审批详情

`GET /v1/tasks/{task_id}/approvals/{approval_id}` 返回当前状态、目标 step/tool、可编辑字段、完整
`proposed_arguments`、有效期和已有决定。服务先校验调用者 tenant 和审批角色；这是审批 UI 获取
完整替换参数的入口。

## 解决审批

`POST /v1/tasks/{task_id}/approvals/{approval_id}` 只接受三个动作。

原样批准：

```json
{"action": "approve", "reason": "Reviewed and approved"}
```

编辑后批准必须先复制 GET 返回的完整 `proposed_arguments`，只修改 allowlist 字段。例如数据库
动作只把 `row_limit` 从 10000 调小到 5000：

```json
{
  "action": "edit",
  "edited_arguments": {
    "query_template_id": "supplier_quality_summary_v1",
    "parameters": {
      "tenant_id": "TENANT-DEMO",
      "start_date": "2026-04-01",
      "end_date": "2026-06-30",
      "supplier_ids": []
    },
    "schema_version": "quality.v1",
    "snapshot_at": "2026-08-02T08:00:00Z",
    "row_limit": 5000
  },
  "reason": "Reduce the bounded result size for this review"
}
```

拒绝：

```json
{"action": "reject", "reason": "Insufficient evidence"}
```

成功响应为 `202`，包含 `approval_id`、`approval_status`、`resolution_action`、`task_id`、最新
`task_status`、`runtime_status`、`status_url`、`resolved_at`、`resolved_by`、`resume_status` 和
`trace_id`。批准/编辑批准会原子创建下一 generation 的 dispatch；接口不 inline resume Graph。
拒绝、过期、撤销和取消不会创建可执行 resume dispatch。

稳定错误映射：400 `INVALID_APPROVAL_ACTION`；403 `APPROVAL_PERMISSION_DENIED`；404
`APPROVAL_NOT_FOUND`；409 `APPROVAL_ALREADY_RESOLVED`、`APPROVAL_STATE_CONFLICT` 或
`APPROVAL_EXPIRED`；422 `INVALID_APPROVAL_REQUEST` 或 `APPROVAL_ARGUMENTS_INVALID`；500
`INTERNAL_ERROR`。错误响应不包含堆栈或完整业务参数。

## 任务管理接口

API 与 CLI 调用 acceptance/query service 和 `ArtifactService`。Route 不直接调用 LangGraph、
Tool、Queue adapter、Repository 或文件路径。独立 Worker 从 PostgreSQL Queue v1 接收 dispatch，
取得数据库 lease 后复用现有受治理 Graph。`TaskStatus` 是业务状态；`runtime_status` 是独立的执行
投影。`WAITING_APPROVAL` 映射为 `SUSPENDED`，不占 Worker 或 lease。

| Method | Path | Request | Response | Success |
|---|---|---|---|---:|
| POST | `/v1/tasks` | `NaturalLanguageTaskSubmission` | `TaskSubmissionResponse` | 202 |
| GET | `/v1/tasks` | `status?`, `limit`, `offset` | `TaskListResponse` | 200 |
| GET | `/v1/tasks/{task_id}` | — | `TaskResponse` | 200 |
| GET | `/v1/tasks/{task_id}/steps` | — | `TaskStepsResponse` | 200 |
| GET | `/v1/tasks/{task_id}/evidence` | — | `TaskEvidenceListResponse` | 200 |
| GET | `/v1/tasks/{task_id}/artifacts` | — | `ArtifactListResponse` | 200 |
| GET | `/v1/tasks/{task_id}/artifacts/{artifact_id}` | — | streamed bytes | 200 |
| POST | `/v1/tasks/{task_id}/cancel` | — | `TaskResponse` | 202 |

查询示例：

```bash
curl 'http://127.0.0.1:8000/v1/tasks?status=COMPLETED&limit=20&offset=0'
curl http://127.0.0.1:8000/v1/tasks/TASK_ID
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/steps
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/evidence
curl http://127.0.0.1:8000/v1/tasks/TASK_ID/artifacts
```

任务列表始终由可信调用上下文约束到当前 tenant 和创建用户，按创建时间降序并用 `task_id` 稳定
排序。`limit` 范围为 1–100，`offset` 必须非负；浏览器不能通过查询参数扩大身份或数据范围。

Artifact 列表只包含 `artifact_id`、安全文件名、格式、媒体类型、checksum、大小和创建时间。
下载接口验证当前 Demo Identity 对 Task 的访问权、Artifact 与 Task 归属、受控根目录、文件大小和
checksum，然后使用流式响应及安全 `Content-Disposition` 返回；不会暴露本地路径。Metadata 存在但
文件缺失或损坏时返回 `410 ARTIFACT_UNAVAILABLE`。

```bash
curl -OJ http://127.0.0.1:8000/v1/tasks/TASK_ID/artifacts/ARTIFACT_ID
```

取消使用冻结状态机的 `CANCEL_REQUESTED` 事件。等待审批、执行、重试、重规划和验证状态可取消；
`CANCELLED` 重复取消幂等；`COMPLETED`/`FAILED` 返回 `409 TASK_NOT_CANCELLABLE`。取消会撤销待决
审批，使旧审批不能恢复 Graph，并移除当前 lease。Worker heartbeat/Tool token 只用于加速观察；
fencing 阻止迟到结果提交。取消是 cooperative cancellation，不承诺强制中断已经进行中的外部调用。

```bash
curl -X POST http://127.0.0.1:8000/v1/tasks/TASK_ID/cancel
```

## 统一错误响应

请求校验、任务、Artifact、审批和未预期错误都使用同一 Schema；FastAPI 默认 422 格式不会直接
暴露：

```json
{
  "error_code": "TASK_NOT_FOUND",
  "message": "Task was not found.",
  "task_id": "T-UNKNOWN",
  "trace_id": "TRACE-...",
  "details": {}
}
```

主要映射：400 非法业务请求；401 未认证；403 权限拒绝；404 Task/Artifact 不存在；409 状态冲突；
410 Artifact Metadata 存在但文件不可用；422 请求 Schema 校验失败；500 未预期错误；503 依赖不可用；
504 依赖超时。Queue capacity 超限返回 `429` 和 `Retry-After`，且不产生半提交 Task。响应不包含
Stack Trace、本地路径、完整 SQL、Token、Prompt 或未过滤第三方响应。

本地默认身份适配器是 Demo Identity，仅用于开发和测试；生产配置强制使用可信网关签名身份。
