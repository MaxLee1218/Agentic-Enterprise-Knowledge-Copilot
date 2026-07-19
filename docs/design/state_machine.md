# Task 状态机冻结设计 v1.0

## 1. 状态定义

| 状态 | 类型 | 进入语义 | 允许的退出方式 |
|---|---|---|---|
| `CREATED` | 暂态 | TaskRequest 已持久化，尚未解释 | 开始理解或取消 |
| `UNDERSTANDING` | 活动态 | 分类请求并形成 TaskContract | 进入规划、失败或取消 |
| `PLANNING` | 活动态 | 创建并验证初始 TaskPlan | 执行、等待审批、失败或取消 |
| `EXECUTING` | 活动态 | 调度可运行步骤并收集 ToolResult/Evidence | 验证、重试、重规划、等待审批、失败或取消 |
| `WAITING_APPROVAL` | 等待态 | 已冻结动作和范围，等待人类决定 | 恢复原阶段、取消或失败 |
| `RETRYING` | 恢复态 | 对同一步骤的瞬时技术错误执行有界重试 | 执行、失败或取消 |
| `REPLANNING` | 恢复态 | 因计划不可执行或验证失败创建更高版本计划 | 执行、等待审批、失败或取消 |
| `VERIFYING` | 活动态 | 检查证据、数字、范围、政策与 Artifact | 完成、重规划、失败或取消 |
| `COMPLETED` | 终态 | 验证全部通过，最终结果已提交 | 无 |
| `FAILED` | 终态 | 不可恢复错误或恢复预算耗尽 | 无；恢复需创建新 Task |
| `CANCELLED` | 终态 | 用户取消、审批拒绝/撤销或等待审批过期 | 无；恢复需创建新 Task |

`WAITING_APPROVAL` 记录 `resume_state`（仅可为 `PLANNING`、`EXECUTING`、`REPLANNING`），批准后回到该阶段的合法后继：规划阶段批准进入 `EXECUTING`，执行阶段批准回到 `EXECUTING`，重规划批准进入 `EXECUTING`。它不是任意跳转机制。

## 2. 状态不变量

1. 任一 Task 同一时刻只有一个状态，状态更新使用版本检查防止并发覆盖。
2. 每次转换都追加事件，包含 task、from、event、to、actor、time、原因和关联对象 ID。
3. 只有通过验证的 TaskContract 才能从 `UNDERSTANDING` 进入 `PLANNING`。
4. 只有通过计划校验且策略允许或审批有效的计划才能进入 `EXECUTING`。
5. 仅当所有必需步骤均有可接受 StepResult 时才能进入 `VERIFYING`。
6. 仅当验证无未解决高严重度问题时才能进入 `COMPLETED`。
7. 终态不可退出，晚到的工具结果只记录为忽略事件，不改变 TaskResult。
8. 取消具有优先级；系统停止调度新调用并尽力取消运行中的调用，然后写入 `CANCELLED`。
9. 重试与重规划均有预算，不能通过互相切换重置预算。

## 3. 正常路径

```text
CREATED
  -> UNDERSTANDING
  -> PLANNING
  -> EXECUTING
  -> VERIFYING
  -> COMPLETED
```

策略要求审批时，在 `PLANNING` 与 `EXECUTING` 之间插入：

```text
PLANNING -> WAITING_APPROVAL -> EXECUTING
```

## 4. 工作流节点输入输出

| 节点 | 必需输入 | 成功输出 | 失败输出/事件 |
|---|---|---|---|
| Task Creation | TaskRequest、认证用户/租户、请求幂等键 | Task ID、初始 TaskState=`CREATED` | 类型化创建错误；未创建半成品 Task |
| Task Understanding | TaskRequest、可信身份与授权范围 | 版本化 TaskContract、分类与约束摘要 | TaskError + `UNDERSTANDING_FAILED` |
| Planning / Plan Validation | TaskContract、ToolDefinition 快照、预算与策略上下文 | 通过校验的 TaskPlan、计划校验记录 | TaskError + `PLAN_INVALID` |
| Policy Check | TaskContract、TaskPlan、用户/租户、数据分类 | allow 决定，或范围绑定的 ApprovalRequest | deny 对应 TaskError，或 `APPROVAL_REQUIRED` |
| Approval | ApprovalRequest、认证审批人、动作指纹、时间 | 不可变审批决定 | `APPROVAL_REJECTED/EXPIRED/REVOKED` |
| Tool Execution | 可运行 TaskStep、已验证输入、ToolDefinition、策略/审批、幂等键 | ToolResult、StepResult、原始来源的安全引用 | ToolResult + TaskError；按错误触发 retry/replan/fail |
| Evidence Aggregation | ToolResult、来源引用、Task/Step/Call ID、数据分类 | 不可变 EvidenceItem 与血缘边 | 证据登记错误；必需证据缺失时不允许完成步骤 |
| Report Composition | 分析 StepResult、Evidence ID、TaskContract 输出要求 | Artifact 元数据、citation map、报告 StepResult | 报告工具的类型化失败 |
| Verification | Contract、计划、全部结果、Evidence、Approval、Artifact | 验证记录和可提交 TaskResult | 可修复/不可修复验证错误事件 |
| Finalization | 通过的验证记录、TaskResult、期望状态版本 | 原子提交 `COMPLETED` 与最终 Artifact 引用 | 提交冲突保持原状态并安全恢复 |
| Cancellation | 当前 TaskState、授权取消主体、运行调用清单 | `CANCELLED` TaskResult、取消审计事件 | 未授权取消返回 Permission TaskError，状态不变 |

每个节点只消费表中对象的已持久化版本或可信执行上下文；节点不得通过 Prompt 文本补齐授权、状态、审批或证据。

## 5. 状态转换表

| From | Event | To | Condition |
|---|---|---|---|
| `CREATED` | `START_UNDERSTANDING` | `UNDERSTANDING` | 请求已持久化且认证上下文有效 |
| `CREATED` | `CANCEL_REQUESTED` | `CANCELLED` | 请求者有取消权限 |
| `UNDERSTANDING` | `CONTRACT_VALIDATED` | `PLANNING` | 类型、年份、季度、租户、能力、输出和约束完整 |
| `UNDERSTANDING` | `UNDERSTANDING_FAILED` | `FAILED` | 请求不受支持、约束冲突或必需信息无法补全 |
| `UNDERSTANDING` | `CANCEL_REQUESTED` | `CANCELLED` | 取消已授权 |
| `PLANNING` | `PLAN_APPROVED_BY_POLICY` | `EXECUTING` | 计划有效，策略允许且无需人工审批 |
| `PLANNING` | `APPROVAL_REQUIRED` | `WAITING_APPROVAL` | 计划有效；动作指纹、范围、计划版本已冻结 |
| `PLANNING` | `PLAN_INVALID` | `FAILED` | 初始计划无法满足 Contract，且没有执行过工具；不消耗重规划预算掩盖设计错误 |
| `PLANNING` | `CANCEL_REQUESTED` | `CANCELLED` | 取消已授权 |
| `WAITING_APPROVAL` | `APPROVAL_GRANTED` | `EXECUTING` | 审批人、角色、计划版本、动作范围与有效期均有效 |
| `WAITING_APPROVAL` | `APPROVAL_REJECTED` | `CANCELLED` | 拒绝代表业务方不授权执行，不归类为系统故障 |
| `WAITING_APPROVAL` | `APPROVAL_EXPIRED` | `CANCELLED` | 到期后禁止使用旧授权 |
| `WAITING_APPROVAL` | `APPROVAL_REVOKED` | `CANCELLED` | 尚未进入不可中断提交阶段；首版无外部副作用 |
| `WAITING_APPROVAL` | `CANCEL_REQUESTED` | `CANCELLED` | 取消已授权 |
| `EXECUTING` | `STEP_SUCCEEDED` | `EXECUTING` | 仍有未完成且依赖可满足的步骤 |
| `EXECUTING` | `BUSINESS_EMPTY_RESULT` | `EXECUTING` | `database_query` 成功返回 0 行；生成空结果证据并继续 |
| `EXECUTING` | `ALL_REQUIRED_STEPS_FINISHED` | `VERIFYING` | 所有必需步骤成功/获准业务结果，Artifact 已生成 |
| `EXECUTING` | `TRANSIENT_FAILURE` | `RETRYING` | 错误可恢复、错误码在 RetryPolicy 中、尝试与截止预算未耗尽 |
| `EXECUTING` | `PLAN_NO_LONGER_EXECUTABLE` | `REPLANNING` | 输入 Schema 或依赖可修复，且重规划预算未耗尽 |
| `EXECUTING` | `LATE_APPROVAL_REQUIRED` | `WAITING_APPROVAL` | 执行前策略发现实际参数需审批；尚未调用目标工具 |
| `EXECUTING` | `NON_RECOVERABLE_FAILURE` | `FAILED` | 权限拒绝、确定性校验错误、不可用且不允许降级，或恢复预算耗尽 |
| `EXECUTING` | `CANCEL_REQUESTED` | `CANCELLED` | 停止调度并完成安全取消 |
| `RETRYING` | `RETRY_READY` | `EXECUTING` | 退避结束，幂等键不变，仍在任务截止时间内 |
| `RETRYING` | `RETRY_BUDGET_EXHAUSTED` | `FAILED` | 尝试次数或总时间预算耗尽 |
| `RETRYING` | `CANCEL_REQUESTED` | `CANCELLED` | 取消已授权 |
| `REPLANNING` | `REVISED_PLAN_VALID` | `EXECUTING` | 新版本 DAG 有效、Contract 不变、无需新审批；可复用步骤通过新输入校验 |
| `REPLANNING` | `REVISED_PLAN_REQUIRES_APPROVAL` | `WAITING_APPROVAL` | 新计划改变受控动作或范围，旧审批失效 |
| `REPLANNING` | `REPLAN_FAILED` | `FAILED` | 无有效计划或重规划预算耗尽 |
| `REPLANNING` | `CANCEL_REQUESTED` | `CANCELLED` | 取消已授权 |
| `VERIFYING` | `VERIFICATION_PASSED` | `COMPLETED` | 证据覆盖、数值、范围、政策、引用及 Artifact 完整性全部通过 |
| `VERIFYING` | `REPAIRABLE_VERIFICATION_FAILURE` | `REPLANNING` | 问题可由重新执行步骤修复，且重规划预算未耗尽 |
| `VERIFYING` | `NON_REPAIRABLE_VERIFICATION_FAILURE` | `FAILED` | 来源不足、政策违规、Artifact 不可恢复或预算耗尽 |
| `VERIFYING` | `CANCEL_REQUESTED` | `CANCELLED` | 完成提交前取消已授权 |

未列出的 `(From, Event)` 组合一律为非法转换，返回 `INVALID_STATE_TRANSITION`，原状态保持不变并记录审计事件。

## 6. 重试策略

- “尝试次数”包含首次调用。知识检索和数据库查询最多 3 次，分析和报告生成最多 2 次。
- 仅 `TECHNICAL_FAILURE`、`TIMEOUT` 且错误码列入步骤 RetryPolicy 时重试；`PERMISSION_DENIED`、Schema 校验失败、SQL 禁止项和业务结果不重试。
- 退避为确定性上限内的指数退避（1 秒、2 秒），不得超过单工具整体截止时间或 Task 截止时间。
- 重试保持相同幂等键、Contract、计划版本和规范化输入。输入改变必须重规划并产生新 step/tool call。
- 每次尝试都有独立 ToolResult；StepResult 指向最终尝试并保留完整历史。

## 7. 重规划策略与终止保证

- 每个 Task 最多 2 次重规划，即计划版本最多为 3。
- 只有 `PLAN_NO_LONGER_EXECUTABLE` 或 `REPAIRABLE_VERIFICATION_FAILURE` 可触发重规划。
- 重规划必须列出验证问题、受影响步骤、被替换步骤和复用证据；不能改变 TaskContract 的业务范围。
- 同一验证规则、同一输入摘要和同一计划结构再次失败时，不允许重复重规划，直接 `FAILED`。
- 新计划若扩大数据、工具或输出范围，必须先生成新版 Contract，并作为新 Task 执行；当前 Task 失败，防止隐式扩权。
- 全局任务截止时间、每步最大尝试次数、最大计划版本和终态不可退出共同保证没有无限循环。

## 8. 指定异常路径

### 8.1 Knowledge Base unavailable

`knowledge_search` 返回可恢复的 `KNOWLEDGE_UNAVAILABLE` 时：

`EXECUTING → RETRYING → EXECUTING`，最多尝试 3 次。若仍不可用，由于报告需要政策/文档证据且首版不允许无依据降级，转为 `FAILED`。若错误明确不可恢复则直接 `EXECUTING → FAILED`。

### 8.2 Database empty result

0 行是成功的业务结果：`ToolResult.status=SUCCESS`，输出 `rows=[]`、`row_count=0`、`empty_result=true`，产生数据库证据。状态保持 `EXECUTING`，分析工具生成零记录/不可计算指标的明确结果，报告说明覆盖范围后进入验证。它不触发重试或失败。

### 8.3 Analysis calculation failed

运行时瞬时故障可按相同输入最多尝试 2 次：`EXECUTING → RETRYING → EXECUTING`。数据类型、分母、单位或 Schema 不合法属于确定性错误，不重试；若现有证据可通过调整依赖或数据准备修复，进入一次有界 `REPLANNING`，否则 `FAILED`。重试耗尽后 `FAILED`。

### 8.4 Approval rejected

`WAITING_APPROVAL → CANCELLED`。拒绝是有意不授权，不是平台故障，因此不使用 `FAILED`；TaskResult 记录安全的拒绝原因及审批 ID，不调用受控工具。

### 8.5 Report verification failed

引用漏绑、报告渲染损坏或可由重新生成修复的数字不一致：`VERIFYING → REPLANNING → EXECUTING → VERIFYING`。同类问题再次发生、证据根本不足、政策违规或重规划预算耗尽时进入 `FAILED`。未经验证的 Artifact 被标记为无效且不会作为完成结果发布。

## 9. 恢复与并发

- 每个成功转换、工具调用提交前后和审批等待前写入检查点。
- 恢复时读取权威状态、计划版本和调用幂等键；已提交但响应丢失的调用先按幂等键查询结果，禁止盲目重复执行。
- 同一 Task 只允许一个调度租约。租约过期可由新执行器接管，但状态版本冲突者停止。
- `WAITING_APPROVAL` 可安全跨进程恢复；审批决定必须与当前动作指纹匹配。
- 进程崩溃不直接改变业务状态；恢复协调器根据最后检查点重建，超过任务截止时间则以 `TASK_DEADLINE_EXCEEDED` 进入 `FAILED`。
