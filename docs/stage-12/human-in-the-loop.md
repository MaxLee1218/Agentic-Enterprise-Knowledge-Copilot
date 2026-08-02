# 阶段 12 Human-in-the-loop v1.1

## 实现范围

阶段 12 实现冻结 v1.1 的 `APPROVE`、`EDIT`、`REJECT` 审批闭环。它没有加入
`create_capa_draft`：v1.1 仍只允许 `knowledge_search`、`database_query`、
`analysis_engine`、`report_generator` 四个工具，只读数据库和内部报告 Artifact 的业务边界不变。

正常演示路径在 Knowledge 检索成功后、`database_query` 首次调用前生成审批。此位置使审批人可将
`row_limit` 从 10000 调小，同时可以证明 Checkpoint 恢复不会重新执行已成功的 Knowledge 步骤。

## 调用链与状态机

```text
POST /v1/tasks（自然语言）
  -> NaturalLanguageTaskService
  -> understand_task -> create_plan -> validate_plan
  -> policy_check
  -> knowledge_search（已提交 Evidence）
  -> policy_check 构造 database_query 完整参数
  -> ApprovalRepository.create(PENDING)
  -> EXECUTING --LATE_APPROVAL_REQUIRED--> WAITING_APPROVAL
  -> SQLite LangGraph Checkpoint
  -> GET approval detail
  -> POST approve | edit | reject
```

- `APPROVE`：`status=APPROVED`，最终参数和指纹等于原提议，事件为
  `APPROVAL_GRANTED`，恢复到 `EXECUTING`。
- `EDIT`：必须提交完整替换参数和原因；当前 v1.1 只允许调小
  `knowledge_search.top_k` 或 `database_query.row_limit`。合法编辑保存新指纹，事件为
  `APPROVAL_EDITED`，恢复到 `EXECUTING`。
- `REJECT`：`status=REJECTED`，事件为 `APPROVAL_REJECTED`，Task 进入冻结终态
  `CANCELLED`，目标及下游工具均不执行。
- `EXPIRED`/`REVOKED` 同样进入 `CANCELLED`，不是人工编辑动作。

非法、部分、扩大范围或 Schema 不兼容的编辑返回类型化错误；Approval 仍为 `PENDING`、Task 仍为
`WAITING_APPROVAL`，Graph 不恢复。

## 参数绑定与恢复

`ApprovalRequest` 在 `contracts/approvals.py` 中绑定 task、tenant、plan version、step、tool/version、
Input Schema 指纹、controlled scope、完整 proposed arguments、editable fields、审批角色、有效期和
乐观锁版本。`EDIT` 保留原参数/指纹并新增完整 resolved arguments/resolved fingerprint，不进行
JSON Patch、深层合并或模型推断。

`ApprovalService` 先重新读取当前 Registry 定义并验证版本、Schema、字段差异和缩小规则，再用
Approval Repository 的 compare-and-swap 产生不可变版本 2。只有一个并发决定能成功；重复、
approve/reject 竞争和旧版本均返回 409。

`LangGraphWorkflowEngine.resume_approval()` 校验当前 Checkpoint 的 tenant、approval、step、plan、
TaskState 和“目标工具尚未调用”，持有执行租约后以 `as_node="policy_check"` 写入最终参数，再沿
正常 Graph 边继续。工具仍通过 Registry、Policy Authorizer、ToolExecutor、Evidence 和 Audit；API
和 Approval Service 不直接执行工具。已经提交的 Knowledge StepResult/Evidence 保留且不重放。

## 持久化、一致性与重启

生产组合使用 `CHECKPOINT_DATABASE_PATH` 指向的 SQLite 文件。Approval 当前版本写入
`workflow_approvals`，每个不可变版本写入 `workflow_approval_history`；正式 DDL 位于
`migrations/0001_approval_requests.sql`。Task、Evidence、Artifact、Audit、租约和 LangGraph
Checkpoint 仍使用各自的既有表。

开发/测试组合可重复初始化本地表；`APP_ENV=production` 不自动建审批表，未先应用 migration 0001
会在启动时明确失败。因此测试便利初始化不能替代生产迁移。

写入顺序采用可恢复的 write-ahead saga：先持久化完整 PENDING Approval，再提交权威 TaskState
转换，随后由 LangGraph 保存节点 Checkpoint。Approval 创建是幂等的，状态和审批恢复均使用 CAS
与执行租约；任何 checkpoint/domain/approval 不一致都 fail closed 为 409，不会调用目标工具。
进程重启后重新创建组合根，Repository 和 SqliteSaver 从同一配置路径加载，审批由原 Checkpoint
继续；不依赖进程内对象。该本地阶段不宣称跨数据库分布式事务或外部调用 exactly-once。

## 身份、权限与审计

`resolved_by` 不来自请求体。`TrustedCallerContext` 提供 user、tenant、roles；API 的开发适配器从
受控 Settings 生成这些值。查看或解决审批都要求 tenant 一致且具备 `required_role`。部署环境必须
用真实认证适配器替换 demo identity，不能把所有用户设为管理员。

审计覆盖 `APPROVAL_REQUESTED`、`APPROVAL_APPROVED`、`APPROVAL_EDITED`、
`APPROVAL_REJECTED`、`APPROVAL_RESUME_STARTED/SUCCEEDED/FAILED`、
`APPROVAL_PERMISSION_DENIED`、`APPROVAL_STATE_CONFLICT` 以及参数校验/过期事件。记录 task、trace、
tenant、approval、step、tool、actor、decision、reason、原始/最终参数哈希和 outcome；不记录完整
参数、Token、Secret、SQL 或原始业务行。

## 验证与已知限制

Executor 在适配器运行前重新验证 Approval 状态、有效期、tenant、step、tool/version、Schema、
resolved arguments 和 resolved fingerprint。Safety Verifier 在完成前再次验证同一绑定和 controlled
scope。离线集成与 smoke 测试覆盖 approve/edit/reject、非法参数、权限、重复/并发 CAS、重启恢复
和 Knowledge 不重放。

当前不支持 CAPA、业务表写入、邮件、采购单、供应商状态变更、任意参数编辑或由建议文本自动
重规划。超出 allowlist 的建议必须拒绝/撤销当前审批，再进入版本化重规划或创建新 Task。
