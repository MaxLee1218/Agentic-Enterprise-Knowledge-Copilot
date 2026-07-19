# Agentic Enterprise Knowledge Copilot 设计基线

## Version

**v1.0 — Frozen**

冻结日期：2026-07-19

## Supported Scenario

唯一支持场景：**Supplier Quality Analysis**。

系统接收明确年份、季度和授权范围的供应商质量分析请求，检索批准的企业知识，查询只读质量数据，执行确定性指标计算，生成内部质量分析报告，并在验证通过后交付带证据链的 Artifact。

规范文档：

1. [业务范围](business_scope.md)
2. [领域模型](domain_model.md)
3. [任务状态机](state_machine.md)
4. [工具契约](tool_contract.md)
5. [桌面演练](walkthrough.md)
6. [设计冲突审查](design_review.md)

这些文件共同构成 v1.0 冻结设计。摘要与详细文档冲突时，以详细文档中的显式契约为准；详细文档之间的冲突必须先通过设计变更流程解决，不能由实现自行选择。

## Domain Model Summary

| 对象 | 冻结职责 |
|---|---|
| TaskRequest | 不可变用户原始请求和审计起点 |
| TaskContract | 将自然语言固定为可验证、可授权的任务边界 |
| TaskPlan | 满足 Contract 的版本化 DAG |
| TaskStep | 有 Schema、依赖和 RetryPolicy 的单一执行节点 |
| TaskState | 任务唯一生命周期状态 |
| StepResult | 一个步骤最终尝试的归一化结果 |
| TaskResult | 终态的对外摘要、Artifact 与 Evidence 引用 |
| ToolDefinition | 注册工具的用途、Schema、风险、时限、审批和幂等定义 |
| ToolResult | 一次工具调用尝试的不可变结果 |
| EvidenceItem | 文档、数据库或计算证据及来源血缘 |
| ApprovalRequest | 与计划版本、动作和范围绑定的人类授权 |
| Artifact | 受控存储中的不可变质量分析报告元数据 |
| TaskError | 安全、类型化、带可恢复属性的统一错误 |

冻结追踪链：

`TaskRequest → TaskContract → TaskPlan → TaskStep → ToolCall/ToolResult → EvidenceItem → StepResult/TaskResult → Artifact`

每个链接都必须由稳定 ID、版本、checksum 或来源引用支撑；不得只依赖日志文本或模型上下文。

## State Machine Summary

冻结状态集合：

`CREATED`、`UNDERSTANDING`、`PLANNING`、`EXECUTING`、`WAITING_APPROVAL`、`RETRYING`、`REPLANNING`、`VERIFYING`、`COMPLETED`、`FAILED`、`CANCELLED`。

正常路径：

`CREATED → UNDERSTANDING → PLANNING → EXECUTING → VERIFYING → COMPLETED`

策略要求时：`PLANNING/EXECUTING/REPLANNING → WAITING_APPROVAL → EXECUTING`。

恢复规则：瞬时技术失败才进入有界 `RETRYING`；计划或验证问题可进入有界 `REPLANNING`；拒绝/过期审批进入 `CANCELLED`；不可恢复错误或预算耗尽进入 `FAILED`。三个终态不可退出。未列入转换表的事件一律非法。

## Tool Contract Summary

| 工具 | 用途 | 风险 | 单次超时 | 最大尝试 | 审批原则 | 幂等边界 |
|---|---|---|---:|---:|---|---|
| `knowledge_search` | 批准知识库检索 | LOW；受限集合 MEDIUM | 10s | 3 | 常规免人工；受限/跨范围需审批 | 输入+工具版本+索引快照 |
| `database_query` | 注册质量视图的参数化 SELECT | MEDIUM | 10s，语句 8s | 3 | 预授权范围可执行；受限/扩大范围需审批 | 模板+参数+Schema+snapshot |
| `analysis_engine` | 确定性质量指标计算 | LOW | 15s | 2 | 输入已获准时无需单独审批 | dataset checksum+指标规范+版本 |
| `report_generator` | 生成内部 PDF/JSON 报告 | LOW | 30s | 2 | 通常免单独审批；继承数据分类策略 | 规范输入+Evidence checksum+模板版本 |

统一结果只有 `SUCCESS`、`BUSINESS_FAILURE`、`TECHNICAL_FAILURE`、`TIMEOUT`、`PERMISSION_DENIED`。数据库 0 行是 `SUCCESS`；工具成功不替代任务验证。所有调用必须通过 Registry、Policy、Approval（需要时）、Executor、Evidence 与 Audit 边界。

## Known Limitations

1. 只支持 Supplier Quality Analysis，不支持通用企业任务。
2. 需要明确年份与季度；不从当前日期静默推断“Q1”。
3. 数据库仅支持两个批准查询模板和注册的 `quality.v1` 只读视图。
4. 分析仅支持缺陷计数、检验数量、缺陷率和期间趋势；不提供预测、因果证明或开放式代码执行。
5. 报告只生成内部 Artifact，不发送邮件、不发布、不触发采购或供应商状态变更。
6. 知识检索依赖固定企业索引快照；必需政策证据不可用时不降级为无来源报告。
7. 不支持多 Agent、跨日自主执行或无限监控。
8. 不包含 MCP 协议互操作；仓库中的 MCP 路径仍是未来边界。
9. 不支持数据库写入及任何不可逆或外部可见业务动作。
10. 重试、重规划、输出行数、供应商数量和运行时间均有固定上限。

## Implementation Rules

1. **Contract first**：先按领域模型定义版本化类型，再实现节点或工具；不得用松散字典替代核心对象。
2. **No prompt-only behavior**：状态、权限、审批、重试、公式、完成条件和失败处理必须由确定性代码实施，不能只写在 Prompt。
3. **Policy before action**：每次工具执行前验证用户、租户、数据范围、风险和审批；模型文本不能授权。
4. **Registry and Executor only**：Agent 节点不得直接调用知识库、数据库、分析器、渲染器或未来协议适配器。
5. **Read only SQL**：模型不提交原始 SQL；仅批准模板解析的参数化单一 SELECT，并执行 AST、Schema、字段、租户、行数和超时校验。
6. **Evidence by default**：每次物质事实、查询或计算必须登记 EvidenceItem；计算 Evidence 必须引用输入 Evidence。
7. **Verify before complete**：只有 verifier 对证据覆盖、数字、范围、政策、引用和 Artifact 完整性全部通过，状态才能为 `COMPLETED`。
8. **Bounded recovery**：实现必须遵守最大尝试、最大计划版本、相同失败指纹和全局 Task 截止，不得形成无界循环。
9. **Approval binding**：审批绑定计划版本、动作指纹、参数范围、审批人角色和有效期；范围改变使旧审批失效。
10. **Immutable audit**：请求、计划版本、工具尝试、审批决定、证据、验证和状态事件只追加或不可变。
11. **Safe failure**：错误使用 TaskError；不暴露密钥、内部堆栈、连接信息、原始敏感行或不必要文档全文。
12. **Recovery correctness**：检查点保存版本和幂等键；恢复先查询已提交结果，不盲目重复调用。
13. **Proportionate tests**：实现必须覆盖每个状态转换、非法跳转、五类工具结果、空数据、零分母、审批拒绝、重试耗尽、重规划耗尽、取消、恢复和端到端证据链。
14. **No scope expansion**：任何新业务场景、工具、指标、数据源、外部副作用、MCP 或多 Agent 行为均需新版本设计审查，不得作为 v1.0 的隐式扩展。
15. **Documentation parity**：实现行为、类型、错误码或限制改变时，必须先提出设计变更并同步 Contract、测试、评估和 ADR。

## Freeze Declaration

后续工程实现必须以本 v1.0 设计基线及其六份规范文档为唯一依据。实现不得自行补充隐含行为、放宽边界、跳过策略/审批/证据/验证，也不得把仓库中的空模块或未来 MCP 脚手架描述为已实现能力。

变更冻结内容必须：提出明确用例和成功指标，分析安全与兼容性影响，更新受影响设计文档与版本，完成冲突审查，获得设计批准，然后才能进入代码实现。
