# Supplier Quality Analysis 桌面演练

## 1. 演练前提

为消除自然语言中“Q1”缺少年份的歧义，本演练使用完整请求：

> Analyze supplier quality issues for suppliers S-100 and S-200 in Q1 2026 and generate a PDF quality analysis report.

认证上下文：用户 `U-QUALITY-01`、租户 `TENANT-A`；用户有两个供应商的常规质量数据读取权。策略规定该报告包含 `CONFIDENTIAL` 供应商表现数据，需要角色 `quality_data_approver` 在执行前批准。以下 ID 均为演练用稳定示例。

## 2. 成功路径

### 2.1 请求接收与理解

| 项目 | 内容 |
|---|---|
| 输入 | 原始请求、认证用户/租户、允许的数据范围 |
| 处理 | 创建 `TaskRequest R-001` 与 Task `T-001`；解析年份 2026、Q1 日期 2026-01-01 至 2026-03-31、供应商 S-100/S-200、PDF 输出 |
| 输出 | `TaskContract`: `supplier_quality_analysis.v1`；四项所需能力；指标与证据要求；审批必需 |
| 状态变化 | `CREATED → UNDERSTANDING → PLANNING` |
| 证据 | 不产生业务 Evidence；审计记录请求、Contract 版本和状态事件 |

### 2.2 计划生成与校验

计划 `P-001`, `planning_version=1`：

| 顺序 | step_id | 类型/工具 | 依赖 | 主要输出 |
|---:|---|---|---|---|
| 1 | `S-KB-01` | KNOWLEDGE_SEARCH / `knowledge_search` | 无 | 政策与供应商资料片段 |
| 2 | `S-DB-01` | DATABASE_QUERY / `database_query` | 无 | 季度质量汇总 dataset |
| 3 | `S-AN-01` | ANALYSIS / `analysis_engine` | `S-DB-01` | 缺陷率和趋势 |
| 4 | `S-RP-01` | REPORT_GENERATION / `report_generator` | `S-KB-01`, `S-AN-01` | PDF Artifact |

计划验证确认 DAG 无环、Schema 可连接、工具已注册、范围不超过 Contract、总重试和时间预算有界。策略为计划生成动作指纹并创建 `ApprovalRequest AP-001`。

| 项目 | 内容 |
|---|---|
| 输入 | TaskContract、ToolDefinition 快照、权限与策略上下文 |
| 输出 | 已验证计划 P-001、待审批 AP-001 |
| 状态变化 | `PLANNING → WAITING_APPROVAL` |
| 证据 | 无业务 Evidence；审计记录计划、策略决定和审批范围 |

审批人 `A-QUALITY-01` 的角色、租户和有效期校验通过，批准 P-001 对 S-100/S-200、2026 Q1 数据的读取与内部报告生成。

| 输入 | 冻结动作指纹、计划版本、审批决定 |
|---|---|
| 输出 | AP-001=`APPROVED` |
| 状态变化 | `WAITING_APPROVAL → EXECUTING` |
| 证据 | 审批是授权记录，不作为业务 Evidence |

### 2.3 企业知识检索

| 项目 | 内容 |
|---|---|
| 输入 | query=`supplier quality defect thresholds and supplier S-100 S-200 requirements`；批准的集合、供应商、日期、`top_k=10`、索引快照 |
| 输出 | 3 个匹配：质量政策 V4 的缺陷率定义、供应商规范版本、季度质量评审规则 |
| 状态变化 | 保持 `EXECUTING` |
| 产生证据 | `E-DOC-01..03`，含 document/version/chunk/checksum 与最小摘录 |

`ToolResult.status=SUCCESS`，StepResult `S-KB-01=SUCCESS`。

### 2.4 SQL 数据查询

| 项目 | 内容 |
|---|---|
| 输入 | 模板 `supplier_quality_summary_v1`；TENANT-A；2026-01-01 至 2026-03-31；S-100/S-200；schema `quality.v1`；snapshot；row_limit=10000 |
| 输出 | 6 行月度聚合；S-100 检验 12,000 件/缺陷 180，S-200 检验 10,000 件/缺陷 80；无截断 |
| 状态变化 | 保持 `EXECUTING` |
| 产生证据 | `E-DB-01`：模板、查询指纹、参数摘要、snapshot、6 行、dataset checksum |

执行前验证 SQL 为单一参数化 SELECT、只访问注册视图并含租户过滤。`ToolResult.status=SUCCESS`，StepResult `S-DB-01=SUCCESS`。

### 2.5 确定性分析

| 项目 | 内容 |
|---|---|
| 输入 | 6 行 dataset、`E-DB-01`、checksum；指标 defect_count/inspected_count/defect_rate/period_over_period_trend；按 supplier/period 分组 |
| 输出 | S-100 季度缺陷率 `180/12000=1.5%`；S-200 为 `80/10000=0.8%`；以及由月度值计算的趋势；无警告 |
| 状态变化 | 保持 `EXECUTING` |
| 产生证据 | `E-CALC-01`：公式、版本、输入 Evidence、分子、分母、维度和结果 |

所有小数按质量指标 v1 的固定精度规则表示。`ToolResult.status=SUCCESS`，StepResult `S-AN-01=SUCCESS`。

### 2.6 报告生成

| 项目 | 内容 |
|---|---|
| 输入 | Task 范围、分析结果、`E-DOC-01..03`、`E-DB-01`、`E-CALC-01`、模板 v1、PDF、zh-CN |
| 输出 | `Artifact A-001`，包含 location、checksum、大小、生成器版本与 citation map |
| 状态变化 | `EXECUTING → VERIFYING`（所有必需步骤完成后） |
| 产生证据 | 不新增来源证据；Artifact 引用已有证据，生成事件进入审计 |

报告将“S-100 缺陷率高于 S-200”标为计算事实；若报告提出原因，只能表述为由文档与数据支持程度明确的观察或待调查假设。

### 2.7 验证与完成

Verifier 执行：

- 范围：年份、季度、供应商与 Contract 一致；
- 数字：报告中的 1.5% 和 0.8% 与计算 Evidence 一致；
- 证据：质量口径引用文档，数据库事实引用查询 Evidence，派生指标引用计算 Evidence；
- 政策：审批范围覆盖实际参数，数据库查询只读；
- Artifact：可读取、PDF 类型正确、checksum/大小匹配、引用全部可解析；
- 限制：声明数据 snapshot、空缺与分析边界。

| 项目 | 内容 |
|---|---|
| 输入 | Contract、P-001、全部 StepResult/ToolResult、Evidence Ledger、A-001 |
| 输出 | 验证通过；`TaskResult T-001`，summary、A-001、全部适用 Evidence ID |
| 状态变化 | `VERIFYING → COMPLETED` |
| 产生证据 | 验证记录属于审计/验证结果，不伪装为业务来源证据 |

端到端链路：`R-001 → T-001/Contract → P-001/S-* → ToolCall/ToolResult → E-* → TaskResult → A-001`。

## 3. 异常路径

### Case 1：Knowledge Base unavailable

| 问题 | 结论 |
|---|---|
| 当前状态 | 首次调用失败时为 `EXECUTING`，随后进入 `RETRYING` |
| 失败位置 | `S-KB-01` / `knowledge_search`，错误 `KNOWLEDGE_UNAVAILABLE` |
| 是否重试 | 是；相同快照、输入与幂等键，最多共 3 次，1 秒/2 秒退避 |
| 是否重新规划 | 否；服务不可用不是计划结构问题，且首版不能绕过必需文档证据 |
| 最终状态 | 第 2/3 次成功则回到 `EXECUTING` 并继续；三次均失败则 `FAILED` |

失败结果含已完成步骤的证据 ID，但不生成成功报告。若错误为权限拒绝则不重试，直接 `FAILED`。

### Case 2：Database empty result

| 问题 | 结论 |
|---|---|
| 当前状态 | `EXECUTING` |
| 失败位置 | 无失败；`S-DB-01` 成功返回 `rows=[]`, `row_count=0`, `empty_result=true` |
| 是否重试 | 否；重复查询不会改变固定 snapshot 的业务事实 |
| 是否重新规划 | 否 |
| 最终状态 | 继续分析与报告，经验证后 `COMPLETED` |

`E-DB-EMPTY-01` 证明在指定查询范围内为 0 行。分析输出 `metrics=[]` 和 warning；报告写“指定范围内未检索到质量记录，无法计算缺陷率”，不能写“缺陷率为 0%”或“无质量问题”。

### Case 3：Analysis calculation failed

演练为运行时瞬时失败 `ANALYSIS_ENGINE_FAILURE`。

| 问题 | 结论 |
|---|---|
| 当前状态 | `EXECUTING → RETRYING` |
| 失败位置 | `S-AN-01` / `analysis_engine` |
| 是否重试 | 是；相同 dataset checksum 最多共 2 次 |
| 是否重新规划 | 瞬时失败不重规划；若是可修复的输入字段/依赖错误，才进入 `REPLANNING`，总计最多 2 次 |
| 最终状态 | 第二次成功则继续至验证；重试耗尽则 `FAILED` |

若失败是分母为零，则不是失败：结果 value=null 并附 warning。若 checksum 不匹配或输入证据越权，属于权限/完整性问题，不重试并 `FAILED`。

### Case 4：Approval rejected

| 问题 | 结论 |
|---|---|
| 当前状态 | `WAITING_APPROVAL` |
| 失败位置 | AP-001 人工决策，受控工具尚未执行 |
| 是否重试 | 否；系统不能重复请求以规避拒绝 |
| 是否重新规划 | 否；不得通过缩写描述或隐藏数据范围绕过审批。用户若提出实质缩小范围的新请求，创建新 Task |
| 最终状态 | `CANCELLED` |

TaskResult 记录审批 ID、`CANCELLED` 和安全的拒绝说明；无业务 Artifact，无数据工具调用。

### Case 5：Report verification failed

演练为报告中的 S-100 缺陷率被渲染为 15%，与 `E-CALC-01` 的 1.5% 不一致。

| 问题 | 结论 |
|---|---|
| 当前状态 | `VERIFYING` |
| 失败位置 | 数字一致性验证规则 `NUMERIC_CLAIM_MISMATCH` |
| 是否重试 | 不直接重试 verifier；相同 Artifact 的结果是确定的 |
| 是否重新规划 | 是；`VERIFYING → REPLANNING`，计划 v2 仅替换报告生成步骤，复用 checksum 未变的有效 Evidence，随后 `EXECUTING → VERIFYING` |
| 最终状态 | v2 报告验证通过则 `COMPLETED`；同一问题再次出现或重规划预算耗尽则 `FAILED` |

无效 Artifact 标记为 `INVALID` 且不进入最终 TaskResult；其 ID 和失败规则保留在审计轨迹中。若失败源于审批范围不覆盖实际数据，则不可通过重生成修复，直接 `FAILED`。

## 4. 演练结论

成功路径覆盖理解、计划、审批、四类工具、三类 Evidence、验证、Artifact 和最终追踪。异常路径分别证明：技术故障有界重试、业务空结果继续、确定性计算不隐式猜测、审批拒绝安全取消、报告缺陷有界重规划。所有路径最终到达 `COMPLETED`、`FAILED` 或 `CANCELLED`，不存在无限等待或无限循环。
