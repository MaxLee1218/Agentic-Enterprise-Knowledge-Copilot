# 设计冲突审查与解决记录 v1.2

## 1. 审查范围与结论

审查对象为：

- [业务范围](business_scope.md)
- [领域模型](domain_model.md)
- [状态机](state_machine.md)
- [工具契约](tool_contract.md)
- [场景演练](walkthrough.md)

结论：v1.2 的对象职责、状态转换、工具 Schema、失败语义和 Supplier Quality 演练一致；所有核心冲突均已解决。没有遗留未定设计项。

v1.2 相对 v1.1 只增加通用的 Interactive Clarification & Resume：非终态
`WAITING_CLARIFICATION`、版本化澄清对象、独立验证上下文、异步暂停/恢复、API 和有界轮数。
Supplier 指标、工具、数据、审批、Evidence、Artifact 和验证行为不变。该变更由 ADR-019 记录，
并与冻结异步架构 ADR-012 至 ADR-017 一致。

v1.1 相对 v1.0 的唯一语义变更仍是审批解决动作 `EDIT`。v1.2 在此基础上增加上述澄清
生命周期；任务类型、四个工具、StepType、ArtifactType、业务分析范围、重试、重规划、证据和
验证语义均未改变。

## 2. Domain Model 审查

| 检查项 | 发现的冲突/风险 | Resolved Issue |
|---|---|---|
| TaskRequest 与 TaskContract | 原始请求和解释后约束容易混为同一对象 | TaskRequest 固定为不可变用户输入；TaskContract 固定为可验证、可授权、版本化的执行边界 |
| TaskPlan 与 TaskStep | 步骤顺序可能被误当作依赖关系 | TaskPlan 展示有序；执行只依据 TaskStep.dependency，且必须为 DAG |
| ToolResult 与 StepResult | 都含 status/output/error，存在重复嫌疑 | ToolResult 表示一次调用尝试；StepResult 汇总一个计划步骤的最终结论并引用重试历史 |
| TaskState 与 StepResult.status | 两套状态可能互相覆盖 | TaskState 只描述全任务生命周期；StepResult 只描述单步骤结果，映射由状态机事件完成 |
| TaskResult 与 Artifact | 报告内容与任务结论可能重复持久化 | TaskResult 保存摘要和 ID 引用；Artifact 保存可交付内容及不可变元数据 |
| EvidenceItem 与 ToolResult.output | 输出可能被误当作已登记证据 | ToolResult 是运行结果；只有经最小化、来源绑定、checksum 和分类处理后才登记为 EvidenceItem |
| ApprovalRequest ownership | 审批究竟属于用户、计划还是工具不清 | 审批归属于 Task，绑定计划版本、step/tool、Schema、原始/最终动作指纹、工具参数范围和审批人决定 |
| Approval EDIT | 修改建议可能被误作任意参数补丁、重规划或扩权 | `EDIT` 是解决动作而非状态；必须提交完整替换参数；v1.1 仅允许调小 `knowledge_search.top_k` 或 `database_query.row_limit`；成功后 `status=APPROVED` 并绑定 resolved action fingerprint |
| Approval immutable history | 编辑可能覆盖原参数并破坏审计 | 保留原始待决版本、proposed arguments/original fingerprint 和不可变解决版本；只允许一次比较并交换解决 |
| metadata | 核心字段可能被塞入自由字典 | 明确 metadata 不得承载权限、状态、审批、Contract 或证据血缘 |
| 对象 ID | 必需字段列表未为部分对象展示 ID | 保留题设业务字段，同时用持久化封套显式补充 evidence_id、approval_id、artifact_id 等稳定关联键 |
| 生命周期 | 覆盖更新会破坏审计 | 请求、计划版本、调用结果、证据、审批决定和最终结果均不可变或只追加；状态通过事件变化 |

Ownership 已在领域模型第 4 节按业务所有者、生命周期和 Repository 逐项固定，不存在无人负责的核心对象。

## 3. State Machine 审查

| 检查项 | 发现的冲突/风险 | Resolved Issue |
|---|---|---|
| 必需状态 | 仅题设状态不足以表达 retry/replan | 增加 `RETRYING` 与 `REPLANNING`，并定义合法入口、出口和预算 |
| 审批恢复 | `WAITING_APPROVAL` 批准后可能返回错误阶段 | 冻结 `resume_state` 和动作指纹；`APPROVE`/合法 `EDIT` 只进入合法 `EXECUTING` 路径，不重放成功前置步骤 |
| 编辑与重规划 | 参数变化通常要求重规划，与审批编辑冲突 | 仅目标首次调用前、同 Contract/Plan/Step/Tool/Schema/范围内的 allowlist 字段可 EDIT；其他变化仍必须重规划/新 Task 并重新审批 |
| 审批拒绝 | `FAILED` 与 `CANCELLED` 语义冲突 | 固定为 `CANCELLED`：拒绝是业务方有意不授权，不是系统故障 |
| 数据库空结果 | 容易被映射成失败并重试 | 固定为 Success + `empty_result=true`，继续分析、报告和验证 |
| 知识库不可用 | 直接失败与重试标准不清 | 仅 `KNOWLEDGE_UNAVAILABLE/TIMEOUT` 有界重试；权限与确定性错误不重试 |
| 分析失败 | 分母为零、坏输入和运行时故障混淆 | 分母为零是 null 值业务结果；坏输入走验证/重规划；瞬时故障有界重试 |
| 报告验证失败 | `VERIFYING ↔ REPLANNING` 可能无限循环 | 计划最多 v3；相同规则+输入+结构再次失败直接 FAILED；Task 全局截止时间不重置 |
| 非法转换 | 任意节点可能直接完成 | 转换表采用显式允许列表；未列组合返回 `INVALID_STATE_TRANSITION` 且状态不变 |
| 死路 | 等待审批或恢复状态可能永久停留 | 审批有失效时间，Task 有全局截止；RETRYING/REPLANNING 有明确成功、失败、取消出口 |
| 终态恢复 | 失败后原 Task 复活会破坏唯一结果 | 三个终态不可退出；需要恢复业务时创建关联的新 Task |
| 并发结果 | 取消后迟到调用可能把任务改为完成 | 状态版本检查；迟到结果仅记审计，不改变终态或 TaskResult |
| 节点边界 | 仅有状态转换不足以验收每个节点的输入输出 | 增加工作流节点输入输出矩阵，覆盖创建、理解、规划、策略、审批、执行、证据、报告、验证、完成和取消 |

正常、重试、重规划、审批、取消和失败路径均能在有限事件数内到达后继或终态。

## 4. Tool Contract 审查

| 检查项 | 发现的冲突/风险 | Resolved Issue |
|---|---|---|
| 状态枚举一致性 | 各工具可能自行命名错误 | 四个工具统一使用五类 ToolResult status 与 TaskError 类型；工具特定原因使用稳定 error_code |
| 输入输出连接 | database 输出与 analysis 输入可能无法追踪 | analysis 必须接收 dataset Evidence ID 与 checksum；report 必须接收分析结果和 Evidence ID |
| SQL 表达 | 接受原始 SQL 会增加越权与注入风险 | 输入仅接受批准的 query_template_id 和参数；模板解析后仍执行 AST/Schema/租户校验 |
| SELECT ONLY | 只写禁止三种 DML 仍可能执行其他副作用语句 | 明确仅单语句 SELECT allowlist，并禁止全部 DML/DDL/DCL、过程、外部函数、多语句和绕过 |
| 空结果 | Tool Failure 与业务事实冲突 | knowledge/database/analysis 的空集合均有显式 `empty_result`；数据库 0 行固定为 Success |
| 超时层级 | 单次 timeout 与重试总时间可能冲突 | 每个工具定义单次和整体截止，且不得超过 Task deadline |
| 幂等 | 报告重试可能生成多个 Artifact | 使用规范输入 hash/版本作为幂等键；Artifact 先临时写入再原子提交 |
| 权限与审批 | 工具自行决定审批会形成旁路 | 策略与 Executor 在调用前校验原始/最终指纹、完整 resolved arguments 和范围；工具只验证传入授权封套，不创建、编辑或扩大审批 |
| 报告 Success | 工具成功可能被误认为任务完成 | report success 仅表示 Artifact 生成，Task 必须进入独立 VERIFYING |
| 分析确定性 | “trend analysis”算法边界不明确 | 首版固定支持指标集合、缺陷率公式、版本、分子/分母和零分母语义 |
| 敏感输出 | 原始行和文档全文可能进入日志/模型 | 限制行数、片段长度和字段；Evidence 最小化；日志只留 ID、指纹和安全摘要 |
| 工具编排 | 工具互调可能绕过 Registry | 明确工具不得调用其他工具或改变状态，所有组合由计划和 Executor 驱动 |

## 5. 跨文档一致性矩阵

| 主题 | Business Scope | Domain Model | State Machine | Tool Contract | Walkthrough |
|---|---|---|---|---|---|
| 唯一场景 | Supplier Quality Analysis | task_type 固定 v1 | 超范围在理解失败 | 仅四个批准工具 | 仅供应商质量示例 |
| 人工审批 | In Scope | ApprovalRequest + APPROVE/EDIT/REJECT resolution | WAITING_APPROVAL | 每工具审批策略与 resolved input binding | 原样批准、编辑恢复与拒绝均演练 |
| 重试 | 有界恢复 | retry_policy/TaskError | RETRYING + 预算 | 技术/超时才可重试 | KB、分析案例 |
| 重规划 | 可修复失败 | planning_version | REPLANNING + 预算 | 输入修复不隐式扩权 | 验证失败案例 |
| 空数据 | 仍可生成报告 | StepResult 可成功 | BUSINESS_EMPTY_RESULT | database Success | Case 2 |
| 追踪 | 成功标准 | 完整对象链 | 每转换有事件 | Evidence 与 checksum | 具体 ID 链路 |
| 完成条件 | 验证后完成 | TaskResult/Artifact | VERIFYING → COMPLETED | report success 非完成 | 验证清单 |

## 6. Resolved Issues 最终清单

1. 已分离原始意图、执行约束、计划、尝试结果、步骤结果和最终结果。
2. 已补齐每个对象的 ownership、生命周期、不可变性和持久化责任。
3. 已定义所有状态、合法转换、终态、不变量、取消和检查点恢复。
4. 已用尝试次数、计划版本、相同失败指纹和全局截止消除无限循环。
5. 已固定数据库空结果、零分母、审批拒绝和验证失败的不同语义。
6. 已统一四个工具的 Schema、风险、超时、幂等、审批和五类结果。
7. 已保证输入输出通过 Evidence ID、checksum、Task/Step/Call ID 连通。
8. 已把权限、审批和策略置于工具执行之前，工具没有旁路。
9. 已完整演练成功路径和五个异常案例，并给出每个案例的最终状态。
10. 已明确 MCP、多 Agent、写数据库和外部发布均不属于本冻结基线。
11. 已定义 `EDIT` 为一次性审批解决动作：完整替换参数、字段 allowlist、双动作指纹、Schema/范围再校验、Checkpoint 恢复和不重放前置步骤；它不改变任何现有业务能力或状态集合。
