# 阶段 12 Human-in-the-loop 实施前审计与 v1.1 复核

## v1.1 复核结论（当前结论）

复核日期：2026-08-02

事实来源：`AGENTS.md`、`docs/design/` 冻结 v1.1、ADR-004 和本文件保留的 v1.0
首次审计证据。

结论：**PASS，可以实施且已按 v1.1 边界实施。**

下方“FAIL / BLOCKED”是设计仍为 v1.0 时形成的历史结论，保留它是为了证明先审计、后变更，
不能再作为当前实施状态使用。v1.1 已在
`docs/design/design_baseline.md`、`domain_model.md`、`state_machine.md`、`tool_contract.md`、
`walkthrough.md`、`design_review.md` 和 `docs/adr/ADR-004-approval-edit-resolution.md` 中只批准
一个变化：ApprovalRequest 增加 `EDIT` resolution action。v1.1 明确不批准附件建议的
`create_capa_draft`，也不新增 CapabilityName、StepType、ArtifactType、TaskStatus、数据写入或外部
副作用。因此阶段 12 使用已有 `database_query.row_limit` 作为合法编辑动作，不实现 CAPA 草稿。

复核后的真实边界与证据：

| 范围 | 当前判断 | 代码与测试证据 |
|---|---|---|
| 分层与依赖方向 | PASS | API 只调用 `ApprovalService`；Graph 只经 `ToolExecutor`；`scripts/check_architecture.py` |
| 自然语言入口 | PASS | `api/routes/tasks.py`、`cli/main.py` 共用 `NaturalLanguageTaskService`；`tests/contract/test_tasks_api_contract.py` |
| Approval Contract | PASS | `contracts/approvals.py` 保存原始/最终参数、双指纹、租户、计划、Schema 和并发版本 |
| Policy 三态 | PASS | `policies/approval.py::PolicyOutcome` 与 `SupplierQualityApprovalPolicy.evaluate()` |
| 暂停与恢复 | PASS | `agent/runtime.py::policy_check()`、`agent/graph.py::resume_approval()`；已完成 Knowledge 步骤不重放 |
| 持久化与并发 | PASS | `persistence/approval_repository.py`、`migrations/0001_approval_requests.sql`；CAS 单赢家测试 |
| API 与权限 | PASS | `GET/POST /v1/tasks/{task_id}/approvals/{approval_id}`；tenant + role 来自可信调用者上下文 |
| Audit 与验证 | PASS | 审批事件记录参数哈希而非完整 payload；Executor 和 Verifier 重新验证 resolved binding |
| 范围控制 | PASS | Registry 仍只有四个 v1.1 工具；没有 `create_capa_draft` 或业务写操作 |

最初 Gap `S12-04` 至 `S12-11` 已由以上实现关闭。`S12-01`/`S12-02` 不是待实现项，而是
v1.1 明确禁止的范围扩展；`S12-03` 已由 ADR-004 和 v1.1 冻结语义解决。完整实施说明见
[`human-in-the-loop.md`](human-in-the-loop.md)。

## v1.0 首次审计（历史记录）

审计日期：2026-08-02  
审计范围：当前工作树、Supplier Quality Analysis v1.0 冻结设计、阶段 10/11 实现、自然语言入口和阶段 12 附件要求。  
结论：**FAIL / BLOCKED（当时禁止进入生产代码实施，已由上方 v1.1 复核取代）**。

## 1. 执行摘要

当前仓库已经具备可验证的阶段 10/11 基础：API 与 CLI 共用自然语言任务服务；原始任务进入 LangGraph 的 Task Understanding、Planner、Plan Validator、Policy Gate、ToolExecutor、Evidence 和 Verifier；SQLite Checkpoint 能在进程重启后恢复普通执行路径，并避免重放已经提交的成功步骤。相关回归测试和全量测试均通过。

但是，当前 Human-in-the-loop 只实现了一个不完整的“停止点”：`GraphNodeRuntime.policy_check()` 可以把任务从 `PLANNING` 转换到冻结状态 `WAITING_APPROVAL`，但不会创建或持久化 `ApprovalRequest`，没有 Approval Repository、Approval Service、Approval API、审批身份权限、审批审计或有效的 Graph Resume Command。即使人为推进状态，`GraphNodeRuntime._execute_attempt()` 仍把 `ToolCall.approval_id` 固定为 `None`，`WorkflowVerifier` 也固定使用 `approvals=()`，因此批准无法绑定到后续执行和最终验证。

更重要的是，本次附件要求新增 `create_capa_draft`，这与 v1.0 冻结设计发生直接冲突：冻结设计只允许四个工具、四种 StepType、两种质量分析报告 Artifact，并明确把供应商整改动作和数据库写操作列为范围外。附件同时要求的 `edit` 审批动作也没有冻结语义，不能在实现层自行发明。依据 `AGENTS.md` 和附件自身的“冻结设计优先”门禁，本次只能提交审计和阻断报告，不能修改生产契约或实现阶段 12。

审计判断：

| 范围 | 判断 | 说明 |
|---|---|---|
| 当前分层与调用方向 | PASS | AST 架构检查通过；API/CLI、Service、Graph、ToolExecutor 和基础设施边界清晰 |
| 阶段 10 Checkpoint/恢复 | PASS | 普通执行路径可跨进程恢复；已有重启与租约测试 |
| 阶段 11 自然语言入口 | PARTIAL | API/CLI 真实进入理解与规划；默认 Mock Planner 仍硬编码四工具模板，且入口会去掉首尾空白 |
| 当前审批停止点 | PARTIAL | 能进入 `WAITING_APPROVAL` 并停止工具，但没有 ApprovalRequest 或恢复闭环 |
| 阶段 12 实施就绪度 | FAIL | 冻结设计冲突为 BLOCKER；审批持久化、权限、策略、事务和恢复能力尚未实现 |

## 2. 事实来源与设计门禁

本审计完整核对了以下冻结设计：

- `AGENTS.md`
- `docs/design/business_scope.md`
- `docs/design/domain_model.md`
- `docs/design/state_machine.md`
- `docs/design/tool_contract.md`
- `docs/design/walkthrough.md`
- `docs/design/design_review.md`
- `docs/design/design_baseline.md`

并核对了当前实现说明：

- `docs/architecture.md`
- `docs/task-lifecycle.md`
- `docs/tool-contract.md`
- `docs/security-model.md`
- `docs/task-understanding-and-planning.md`
- `docs/langgraph-workflow.md`
- `docs/deterministic-workflow.md`
- `docs/evidence-and-verification.md`
- `README.md`
- `pyproject.toml`

仓库内没有找到独立的“阶段 12 实施计划”文件；阶段 10/11 的边界分别散见 `docs/langgraph-workflow.md`、`docs/task-understanding-and-planning.md`、`docs/llm-architecture.md`、`README.md` 和 ADR。阶段 12 的详细要求来自本次附件，但附件不能覆盖冻结设计。

## 3. 项目结构清点

| 职责 | 真实位置 | 关键类/函数 | 判断 |
|---|---|---|---|
| 领域契约 | `src/copilot/contracts/` | `TaskRequest`、`TaskContract`、`TaskPlan`、`TaskState`、`ToolCall`、`ApprovalRequest` | PASS；集中且不导入 FastAPI/SQLAlchemy/LangGraph |
| Agent Graph | `src/copilot/agent/graph.py` | `build_agent_graph()`、`LangGraphWorkflowEngine.submit()`、`resume()` | PASS；调度与 Checkpoint 边界明确 |
| Agent 状态 | `src/copilot/agent/state.py` | `AgentGraphState`、`checkpoint_serializer()` | PARTIAL；没有 pending approval/approval 决定 |
| Agent 节点 | `src/copilot/agent/nodes/`、`src/copilot/agent/runtime.py` | 节点薄包装；逻辑集中在 `GraphNodeRuntime` | PASS；未发现节点直接执行 SQL/HTTP |
| Application Service | `src/copilot/services/task_service.py`、`src/copilot/services/workflows/` | `NaturalLanguageTaskService`、`SupplierQualityWorkflowService` | PASS；API/CLI 共用自然语言服务；旧结构化服务仍作为兼容/回归路径 |
| Approval Service | `src/copilot/services/approval_service.py` | 空文件 | FAIL；阶段 12 尚未实现 |
| 工具框架 | `src/copilot/tools/` | `ToolRegistry`、`ToolExecutor`、`ToolAuthorizer` | PASS（当前四工具）；所有真实调用经过 Executor |
| 工具适配器 | `src/copilot/tools/knowledge/`、`database/`、`analytics/`、`reporting/` | `KnowledgeTool`、`DatabaseTool`、`AnalyticsTool`、`ReportTool` | PASS；没有工具互调 |
| Policy | `src/copilot/policies/engine.py`、`offline.py` | `DenyByDefaultToolAuthorizer`、`OfflineSupplierQualityAuthorizer` | PARTIAL；没有结构化 ALLOW/REQUIRE_APPROVAL/DENY 决策 |
| Persistence | `src/copilot/persistence/task_repository.py`、`audit_repository.py`、`artifact_repository.py`、Evidence Ledger | `InMemoryWorkflowRepository` 等 | PARTIAL；可选 SQLite 持久化真实存在，但 Approval Repository 不存在且依赖运行时建表 |
| Checkpoint | `src/copilot/bootstrap/container.py`、LangGraph `SqliteSaver` | `checkpoint_serializer()`、`SqliteSaver` | PASS；`src/copilot/persistence/checkpoint.py` 是空占位，不是实际实现位置 |
| Evidence/Verification | `src/copilot/evidence/` | `InMemoryEvidenceLedger`、`WorkflowVerifier`、`CompositeVerifier` | PASS（现有只读报告场景）；Approval 输入固定为空 |
| Observability | `src/copilot/observability/`、两个 Audit Repository | `WorkflowAuditRecord`、`ToolAuditRecord` | PARTIAL；结构化 Audit 已实现，通用 observability 文件仍为空 |
| API | `src/copilot/api/` | `POST /v1/tasks`、统一错误处理 | PASS（任务提交）；Approval route 是空文件且未注册 |
| CLI | `scripts/run_task.py`、`src/copilot/cli/main.py`、`src/copilot/bootstrap/cli.py` | `create_app()`、`_run()` | PASS；支持直接自然语言位置参数/`--task` |
| 组合根 | `src/copilot/bootstrap/container.py` | `build_workflow_container()`、`build_application()` | PASS；依赖集中装配，业务模块不自行读取环境变量 |
| Migration | `migrations/` | 目录为空 | FAIL（阶段 12）；没有审批表正式迁移 |
| Tests | `tests/unit/`、`integration/`、`contract/`、`smoke/` | 400 个通过、1 个 live 测试跳过 | PASS（当前基线）；没有 Approval resolve/resume 测试 |
| Scripts | `scripts/` | `run_task.py`、`check_architecture.py`、`check_docs.py` | PASS；业务逻辑不在脚本中 |

合理的目录调整包括：Checkpoint 的真实装配在 `bootstrap/container.py` 而不是空的 `persistence/checkpoint.py`；SQLite 业务存储由命名为 `InMemory*` 的 Repository/Ledger 可选启用。这些位置与计划中的推荐目录不同，但当前调用方向仍然清楚。命名会降低可读性，但不是阶段 12 的首要阻断项。

## 4. 依赖方向审计

真实调用方向为：

```text
API / CLI
  -> NaturalLanguageTaskService
  -> LangGraphWorkflowEngine
  -> GraphNodeRuntime
  -> PlanValidator / policy gate / ToolExecutor
  -> ToolRegistry
  -> injected tool adapter
  -> Evidence / Audit / Artifact / Workflow Repository
  -> WorkflowVerifier
```

核查结果：

- `src/copilot/contracts/` 未导入 FastAPI、SQLAlchemy 或 LangGraph。
- API DTO 位于 `src/copilot/api/schemas/tasks.py`，与 `TaskRequest`/`TaskContract` 分离。
- `src/copilot/api/routes/tasks.py::submit_task()` 只做 DTO 到 `NaturalLanguageTaskCommand` 的适配并调用 Service。
- Graph 节点只调用注入的 `GraphNodeRuntime`；未发现 Graph 节点直接执行 SQL 或 HTTP。
- `GraphNodeRuntime._execute_attempt()` 唯一通过 `ToolExecutor.execute()` 执行工具。
- `OfflineSupplierQualityAuthorizer` 依赖通用 `ToolCall`/`ToolDefinition`，不依赖具体工具类。
- Repository 返回领域契约，不泄漏 SQLAlchemy ORM 模型。
- `src/copilot/config.py` 是生产包内唯一读取环境配置的入口，组合根消费 `Settings`。
- `scripts/check_architecture.py` 的 AST 分层检查通过。
- 当前测试默认使用 MockLLM、Mock Knowledge 和隔离 SQLite；普通测试不依赖真实外部服务。

已知限制：`scripts/check_architecture.py` 验证层间依赖和禁用 SDK，但不显式检测同层模块循环。全量导入和 400 个测试没有暴露运行时循环，仍建议未来把同层循环检测加入脚本。判断为 LOW。

## 5. 阶段 10 LangGraph 前置能力审计

### 5.1 已实现

- `src/copilot/agent/graph.py::build_agent_graph()` 注册显式节点和条件边。
- `LangGraphWorkflowEngine.submit()` 在自然语言理解之前持久化 `TaskRequest` 和初始 `CREATED` 状态。
- `LangGraphWorkflowEngine.resume()` 从 `tenant_id:task_id` Checkpoint 恢复，并核对 Checkpoint 与权威 `TaskState`。
- `src/copilot/agent/state.py::checkpoint_serializer()` 对允许反序列化的领域类型采用显式 allowlist。
- `src/copilot/bootstrap/container.py::build_workflow_container()` 使用 `SqliteSaver`，同时装配持久化 Task、Evidence、Artifact 和 Audit 存储。
- `InMemoryWorkflowRepository.acquire_execution()` 提供单任务执行租约；`commit_transition()` 使用状态版本比较并交换。
- `tests/integration/test_langgraph_workflow.py` 验证每个安全边界重启、成功步骤不重复执行、终态不可恢复、并发租约冲突和审批前不执行工具。

### 5.2 尚未覆盖审批恢复

- `test_approval_required_stops_before_any_tool_execution()` 只证明任务停止在 `WAITING_APPROVAL`，没有批准后的恢复测试。
- 当前 `policy_check()` 到 `persist_result()` 后 Graph 已到 END；没有把审批决定注入 Checkpoint 的 Command/状态更新路径。
- `AgentGraphState` 不包含 ApprovalRequest、pending approval、approval decision 或批准后的参数。
- 当前恢复语义是普通节点中断恢复，不是审批恢复。

阶段 10 对普通路径的结论为 PASS；作为阶段 12 前置的“审批后恢复”结论为 FAIL。

## 6. 阶段 11 Task Understanding 与 Planner 审计

### 6.1 Task Understanding

- `NaturalLanguageTaskService.prepare()` 创建不可变 `TaskRequest` 和独立 `TrustedTaskContext`。
- `GraphNodeRuntime.understand_task()` 把 `state["request"]` 交给注入的 Planning Service。
- `LLMPlanningService.understand()` 只允许模型提供业务语义；tenant、data scope、read-only、approval 和 deadline 来自可信上下文。
- 缺少年份或季度时返回 `TASK_INFORMATION_MISSING`，按冻结状态机走 `UNDERSTANDING -> FAILED`，不会执行工具。

偏差：`validate_task_text()` 会 `strip()` 首尾空白，之后才保存 `TaskRequest.raw_input`。这不满足“原始自然语言原样保留”的最严格解释，也弱于冻结模型中的“保留原貌”。严重度 MEDIUM；尚未修改。

### 6.2 Planner 与 Plan Validator

- `GraphNodeRuntime.create_plan()` 调用 `LLMPlanningService.create_plan()`，不是 API/CLI 直接选择工具。
- `PlannerToolManifestBuilder` 从当前 `ToolRegistry` 动态构建工具清单与 Schema。
- `PlanValidator.evaluate()` 校验 task/version/step limit、Contract capability、Registry membership、StepType、完整 Schema 和 Supplier Quality 依赖。
- 真实 DeepSeek provider 路径可生成候选计划；所有候选仍经过确定性 Validator。

偏差：默认 `LLM_PROVIDER=mock` 时，`OfflineMockLLM._plan()` 硬编码四个工具名称、顺序、依赖和重试策略。虽然它经由 Planner 接口并从 Manifest 复制 Schema，但“默认路径不是固定计划”这一阶段 12 验收项只能判为 PARTIAL。旧的 `SupplierQualityAnalysisPlanFactory` 还保留给结构化兼容/回归服务，但 API/CLI 不调用该服务。

## 7. 自然语言入口端到端审计

### 7.1 CLI 调用链

```text
scripts/run_task.py
  -> copilot.bootstrap.cli.app
  -> copilot.cli.main.create_app() / _run()
  -> build_application()
  -> NaturalLanguageTaskService.submit()
  -> LangGraphWorkflowEngine.submit()
  -> validate_request
  -> understand_task
  -> classify_task
  -> create_plan
  -> validate_plan
  -> policy_check
  -> ToolExecutor / Evidence / Verification
```

CLI 只要求自然语言位置参数或 `--task`；不要求 task_type、表名、工具名、Planner 参数或计划步骤。

### 7.2 API 调用链

```text
POST /v1/tasks {"task": "..."}
  -> NaturalLanguageTaskSubmission
  -> routes.tasks.submit_task()
  -> NaturalLanguageTaskService.submit()
  -> 与 CLI 相同的 LangGraph
```

`tests/contract/test_tasks_api_contract.py` 证明 OpenAPI 的唯一必需字段是 `task`，且没有 goal、entities、time_range、steps、tool 或 arguments。

### 7.3 验收输入实测

| 输入 | 结果 | 判断 |
|---|---|---|
| 中文“分析第二季度……”（没有年份） | `FAILED / TASK_INFORMATION_MISSING`，0 个工具调用 | 符合冻结设计；不得静默假定年份 |
| 英文“Analyze Q2 …”（没有年份） | `FAILED / TASK_INFORMATION_MISSING`，0 个工具调用 | 符合冻结设计 |
| 中文“分析 2026 年第二季度……” | `COMPLETED / Verification PASSED` | 自然语言入口真实可用 |
| 英文“Analyze Q2 2026 …” | `COMPLETED / Verification PASSED` | 自然语言入口真实可用 |
| 带 UPDATE/shell/Python/关闭审批的恶意任务 | 测试只得到四个冻结工具，数据库输入无 raw SQL | 用户文本不能扩展工具或权限 |

### 7.4 八个明确答案

1. 当前是否真正支持用户直接输入自然语言？**是，但必须满足冻结的明确年份/季度要求。**
2. CLI 是否支持？**是。**
3. API 是否支持？**是。**
4. 是否仍在入口要求结构化业务参数？**API/CLI 否；旧回归服务仍接受 `SupplierQualityCommand`，但不是默认入口。**
5. 自然语言是否真实进入 Task Understanding？**是；经 `TaskRequest.raw_input` 进入 `LLMPlanningService.understand()`。**
6. Planner 是否真实生成执行计划？**调用链是；DeepSeek 路径是候选生成，默认 OfflineMock 的计划内容仍是固定四步。**
7. 默认路径是否仍回退到固定计划？**不调用旧 PlanFactory，但默认 OfflineMock Planner 内部仍硬编码四步，因此严格判断为 PARTIAL。**
8. 哪些问题阻断阶段 12？**冻结设计禁止新工具/写型 Artifact；审批闭环、持久化、权限、策略决策、参数绑定和恢复均缺失。**

## 8. 阶段 12 Gap Analysis

| ID | 严重度 | 发现 | 代码证据 | 影响 |
|---|---|---|---|---|
| S12-01 | BLOCKER | `create_capa_draft` 不在冻结四工具内 | `docs/design/tool_contract.md`、`CapabilityName`、`ToolRegistry.__init__()` | 未经设计变更不能注册或计划第五个工具 |
| S12-02 | BLOCKER | CAPA 草稿需要新 Step/Artifact 语义 | `StepType`、`ArtifactType`、`TaskContract.required_capabilities` | 修改会扩大冻结领域枚举和交付物范围 |
| S12-03 | BLOCKER | `edit` 审批动作没有冻结语义 | `ApprovalStatus`、`ApprovalRequest`、冻结状态机 | 不能自行决定参数合并、指纹重绑或审批状态 |
| S12-04 | HIGH | Policy gate 不创建 ApprovalRequest | `GraphNodeRuntime.policy_check()` | `WAITING_APPROVAL` 没有可审核的动作、参数或审批 ID |
| S12-05 | HIGH | Approval Repository/Service/API 均不存在 | 空的 `approval_service.py`、`api/routes/approvals.py`；无 repository | 无法读取、并发解决、审计或恢复审批 |
| S12-06 | HIGH | 执行永远不绑定审批 | `GraphNodeRuntime._execute_attempt(): approval_id=None` | 审批 A 无法安全授权任何 ToolCall |
| S12-07 | HIGH | Verifier 永远看不到审批 | `WorkflowVerifier.verify(): approvals=()` | 审批要求存在时最终 Safety Verification 必然缺少审批上下文 |
| S12-08 | HIGH | 身份是 server-owned demo adapter，且没有审批角色 | `api/dependencies.py::get_caller_context()` | 不能实现真实 approver/tenant/permission 校验 |
| S12-09 | HIGH | 没有审批迁移或事务策略 | 空 `migrations/`；Repository 启动时 `CREATE TABLE IF NOT EXISTS` | 不能满足生产迁移、原子请求/状态/Checkpoint 一致性 |
| S12-10 | HIGH | 等待态没有 Approval Resume 路由 | `route_after_policy()`、`persist_result()`、`LangGraphWorkflowEngine.resume()` | 只能停，不能 approve/edit/reject 后按冻结事件恢复 |
| S12-11 | MEDIUM | Policy 没有结构化三态决策 | `DenyByDefaultToolAuthorizer.authorize()`、`OfflineSupplierQualityAuthorizer.authorize()` | 无稳定 ALLOW/REQUIRE_APPROVAL/DENY 结果与原因 |
| S12-12 | MEDIUM | 工具级 risk/read-only/approval metadata 未被 Graph policy 综合评估 | `policy_check()` 只检查 Contract flag | 未来写工具可能出现策略空洞 |
| S12-13 | MEDIUM | 默认 Mock Planner 固定四步 | `OfflineMockLLM._plan()` | 不满足附件的严格“非固定计划默认路径”标准 |
| S12-14 | MEDIUM | TaskRequest 保存前去掉首尾空白 | `validate_task_text()` | 原始输入不能字节级复现 |
| S12-15 | MEDIUM | ToolResult 冲突保护有不可达分支 | `InMemoryWorkflowRepository.save_tool_result()` 中 `return` 后的 `raise` | 纯内存模式不能可靠拒绝同 call ID 的不一致结果 |
| S12-16 | LOW | 架构脚本不检测同层循环 | `scripts/check_architecture.py` | 当前未发现循环，但门禁覆盖不完整 |
| S12-17 | LOW | `docs/langgraph-workflow.md` 的 Running 示例仍是旧结构化 CLI | 文档末尾 `--supplier-id`/`--time-range` | README 正确，但局部文档漂移 |

## 9. 冻结设计冲突

### 9.1 新工具与业务范围

- `docs/design/domain_model.md` 的 `ToolDefinition` 明确“首版仅四个批准工具”。
- `docs/design/design_baseline.md` 的 Tool Contract Summary 只列 `knowledge_search`、`database_query`、`analysis_engine`、`report_generator`，并要求任何新工具先走设计变更。
- `docs/design/tool_contract.md` 只定义上述四个工具及其跨工具约束。
- `src/copilot/contracts/enums.py::CapabilityName` 与 `ToolRegistry` 的默认 allowlist 只允许上述四个名称。

因此 `create_capa_draft` 不能作为“最小实现”直接加入 Registry；它是冻结能力集合的扩展。

### 9.2 Step 与 Artifact

- `StepType` 只有 Knowledge、Database、Analysis、Report 四类。
- `ArtifactType` 只有 `QUALITY_ANALYSIS_REPORT_PDF/JSON`。
- `docs/design/domain_model.md` 规定首版业务 Artifact 是质量分析报告。

CAPA Draft 若成为计划步骤或 Artifact，必须明确新增 StepType/Capability/ArtifactType 或设计一个不破坏既有语义的版本化替代方案。这属于领域契约变更，不能靠兼容层隐藏。

### 9.3 写操作与供应商整改动作

`docs/design/business_scope.md` 把自动数据库写入以及“供应商整改动作”列为 Out of Scope；`docs/design/design_baseline.md` 再次声明不支持数据库写入和不可逆/外部可见业务动作。即使 `create_capa_draft` 只写 Artifact、不写核心业务表，它仍是新的业务动作和交付物，冻结文档没有授权。

### 9.4 审批 edit 语义

冻结 `ApprovalRequest` 定义 PENDING/APPROVED/REJECTED/EXPIRED/REVOKED，并绑定计划版本、动作指纹、范围和有效期；没有 `edit` 动作、resolved arguments 或 Merge 规则。附件要求 edit 后直接 APPROVED，会改变“批准的是哪个动作指纹”的含义。该语义必须先写入新设计版本，并明确是更新原请求、创建替代请求还是形成新的 action fingerprint。

### 9.5 状态名称与拒绝终态

附件示例中的 `NEEDS_APPROVAL` 不能加入；实际必须复用冻结 `WAITING_APPROVAL`。审批拒绝后的终态已经冻结为 `CANCELLED`，不能选择 `FAILED` 或添加 `REJECTED` Task 状态。

## 10. 为什么不能安全继续

如果直接实施附件，会至少修改 `CapabilityName`、`StepType`、`ArtifactType`、TaskContract 能力集合、Planner/Validator 规则、Tool Registry allowlist、ApprovalRequest 语义、Verifier 和持久化 Schema。它不是在既有审批边界内补代码，而是在扩展 Supplier Quality v1.0 的业务范围和领域模型。

`AGENTS.md` 要求这种变更先更新所有受影响设计文档、解决跨文档冲突、版本化基线并获得批准。本次没有授权修改冻结设计，也没有新的已批准 baseline version。继续实现会形成两套领域语义或让实现领先于唯一设计权威，因此已停止。

## 11. 实际验证命令与结果

| 命令 | 结果 |
|---|---|
| `python scripts/check_architecture.py` | 环境失败：系统没有 `python` 命令 |
| `python3 scripts/check_architecture.py` | 环境失败：系统 Python 3.9 缺少 `StrEnum` |
| Codex Python 3.12 运行 `scripts/check_architecture.py` | PASS |
| Codex Python 3.12 运行 `scripts/check_docs.py` | PASS |
| `.venv/bin/python -m pytest -q` | PASS：400 passed，1 skipped（live test），1 deprecation warning |
| `.venv/bin/ruff check .` | PASS |
| `.venv/bin/ruff format --check .` | PASS：286 files already formatted（写审计前基线） |
| `.venv/bin/mypy` | PASS：286 source files |
| `.venv/bin/python evaluation/run_eval.py --smoke` | PASS |
| `.venv/bin/python -m build`（sandbox） | 首次失败：隔离环境无法联网获取 setuptools |
| `.venv/bin/python -m build`（批准联网后重试） | PASS：sdist 与 wheel 构建成功 |
| 关键阶段 10/11/自然语言/API/CLI 定向测试 | PASS：34 passed，1 warning |
| 中文/英文无年份 CLI 实测 | 预期失败：`TASK_INFORMATION_MISSING`，无 Artifact |
| 中文/英文含 2026 Q2 CLI 实测 | PASS：`COMPLETED`、Verifier `PASSED` |

全量测试中的 1 个 skip 是显式 live Enterprise RAG 测试，不是本次新增或人为跳过。没有删除、弱化或新增 skip。

## 12. 本次修改范围

本次只新增本审计文件：

- `docs/stage-12/pre-implementation-audit.md`

没有修改生产代码、领域枚举、冻结设计、数据库 Schema、API、测试或既有文档；没有实现或声称实现阶段 12。

## 13. 需要项目所有者决定的问题

开始实现前至少需要一个明确批准的新设计版本，回答：

1. `create_capa_draft` 是否正式进入 Supplier Quality 场景，还是作为新场景/新版本能力？
2. 新工具对应哪个 `CapabilityName`、`StepType` 和 `ArtifactType`？TaskContract 是否允许四个读取/报告能力之外的第五项能力？
3. CAPA Draft 是任务最终交付物、附属 Artifact，还是仅审批后生成的中间 Artifact？Verifier 如何判断完整性？
4. “edit” 是审批动作、创建替代 ApprovalRequest，还是导致旧审批失效并重新审批？最终 action fingerprint 如何计算？
5. Tool 参数是在 planning 时冻结，还是在执行前由 `StepInputBuilder` 构造？审批绑定哪个规范化参数快照？
6. ApprovalRequest、TaskState 转换和 LangGraph Checkpoint 使用同库事务、outbox/recovery protocol，还是其他一致性方案？
7. 认证/授权适配器的最小生产接口是什么？demo identity 是否仅限 development/test？
8. 正式迁移框架采用 Alembic 还是版本化 SQLite migration runner？
9. 新 baseline 的版本号、兼容策略、ADR、安全评审、评估数据和验收责任人是谁？

## 14. 阶段 12 完成定义判定

结论：**不满足阶段 12 完成定义。**

已满足的前置项：

- 有文件证据的现状审计；
- CLI/API 可接收单一自然语言任务；
- 自然语言真实进入 Task Understanding；
- Planner 路径和确定性 Plan Validator 已接通；
- Planner 不能执行 Registry 之外的工具；
- 普通 LangGraph Checkpoint/重启恢复可用；
- 当前任务可安全停止在 `WAITING_APPROVAL` 且停止前没有工具执行。

未满足且本次未实现的项：

- 批准后的新设计基线；
- `create_capa_draft` 合法领域契约、注册和执行；
- 完整 ApprovalRequest 参数绑定；
- Approval Repository、Migration、Service、API；
- approve/edit/reject 语义和并发控制；
- 审批身份权限与跨租户隔离；
- 审批审计事件；
- Approval 与 ToolCall/Verifier 绑定；
- 审批后 Checkpoint 恢复及应用重启恢复；
- 阶段 12 单元、契约、集成和 Smoke 测试；
- 默认 Mock Planner 非固定计划要求。

下一步必须先执行冻结设计变更流程；在新基线批准前，不应开始阶段 12 生产实现。
