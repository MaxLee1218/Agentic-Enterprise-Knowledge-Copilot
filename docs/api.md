# HTTP API

## 提交自然语言任务

`POST /v1/tasks` 的唯一必需字段是 `task`。当策略无需人工决定时返回 201；产生待审批动作时返回
202、`status=WAITING_APPROVAL` 和 `pending_approval_id`。

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

成功响应包含 `approval_id`、`approval_status`、`resolution_action`、`task_id`、最新
`task_status`、`resolved_at`、`resolved_by`、`resume_status` 和 `trace_id`。当前离线工作流同步继续到
终态；接口不会假定所有部署都同步完成。

稳定错误映射：400 `INVALID_APPROVAL_ACTION`；403 `APPROVAL_PERMISSION_DENIED`；404
`APPROVAL_NOT_FOUND`；409 `APPROVAL_ALREADY_RESOLVED`、`APPROVAL_STATE_CONFLICT` 或
`APPROVAL_EXPIRED`；422 `INVALID_APPROVAL_REQUEST` 或 `APPROVAL_ARGUMENTS_INVALID`；500
`INTERNAL_ERROR`。错误响应不包含堆栈或完整业务参数。
