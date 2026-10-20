# Supplier Quality validation 缺陷修复记录

针对首次验证发现的 [#13](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/13)、
[#14](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/14)、
[#15](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/15)。原始自动 11/16、
PDF 审计 8/16、V14 失败及 V16 等待均保留，详见[首次结果](supplier-quality-validation-result.md)。
独立回归与 README 首页证据跟踪 [#16](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/16)。

## 根因与最小修正

| Issue | 控制测试复现的根因 | 修正 |
|---|---|---|
| #13 | 任务类型识别漏掉带供应商代码的 `quality report`、`quality of` 措辞，进入 Task 前即拒绝 | 增补有界且需要 supplier/供应商代码的语言信号；不注入 task_type、oracle 或权限 |
| #14 | 真实 PDF 第 2 页图表标题硬编码 Q2，嵌入模型正确也无法覆盖可见错误 | 标题取可信 ReportDocument.scope.quarter，不改图表数值或计算 |
| #15 | 中间 Schema 的“年份/季度必须同时存在或同时缺失”验证使部分候选直接失败；确定性解析/上下文只保留完整期间，导致部分事实在回答前丢失 | 中间候选可保留缺失字段；只从确定性解析接受年份/季度并逐项持久化，完整契约前仍不规划/执行 |

V14 原始记录只保留安全的 `LLM_SCHEMA_VALIDATION_ERROR`，没有 provider 原文；部分候选 Schema
失败由不同控制请求确定性复现，不能据此声称已还原 V14 原响应的具体字段。V16 同类控制记录显示，
恢复前的 context 只有 supplier_ids，确实缺少原请求已明确的年份；无需修改 Worker、Queue 或
Checkpoint 权威来修复理解层的问题。

保留不支持任务、越权供应商、非法字段的安全失败；模型时间不作为授权或日历事实来源。供应商编号
`SUP-2027` 不被误识别为年份；带单位/明确字段名的业务计数也不能补全年份。
期间 token 独立识别并校验，覆盖带标点的非法年份/季度以及契约允许的 2100–2999 年。
非法原请求或非法澄清答案直接失败，不能回退到之前的合法期间。原始 raw_input 不变，TaskContract 仍要求完整年份/季度及授权范围；
澄清响应仍经同一 Task 的既有 PostgreSQL dispatcher/Worker 进入 UNDERSTANDING。

## 修复前后证据

使用既有开发源数据及不同控制措辞，没有预跑新的 V01–V20 产品结果：

- `tests/integration/test_natural_language_intake.py`：两种有效措辞先出现输入拒绝，再通过真实
  Task Service/Graph/Executor 交付；读取实际 PDF 第 2 页，Q1/Q3 修复前失败，Q2 为对照。
- `tests/integration/test_llm_planning_workflow.py`：部分模型候选原先无法暂停；修复后进入
  WAITING_CLARIFICATION，零工具、无 Contract/Plan、原请求不变。四位供应商代码的保护测试
  阻止用代码中的数字补全年份。
- `tests/integration/evaluation/test_pilot_suite.py`：独立的缺失年份和缺失季度请求，补充一个
  预置答案后原先仍等待；修复后经真实 PostgreSQL Worker、同一 Task 完成，仅 S-100，15/15
  数值断言通过。现有越权/供应商保持测试继续通过。
- 三份控制 PDF 的实际第 2 页已渲染检查：Q1/Q2/Q3 标题正确，未见裁切、重叠或字号问题。

完整本地门槛：**1014 passed、35 skipped**（需要单独环境的门槛保留原 skip 条件）；Ruff、format、
Mypy（540 source files）、docs/architecture、actionlint、distribution build 均通过。
Supplier mock **30/30**、AP mock **25/25**，MCP interoperability **13/13**、safety **12/12**。
本节是离线修复证据，不是新的真实模型成绩。复核、提交与后续回归结果在 Issues 留档。

复核校准：遗留 30-case 开发集的 `invalid-quarter` 同时要求非法季度失败和发起澄清，
后者违反 `docs/design/business_scope.md:25`。开发集升至 **1.2.1**，仅改该用例的澄清期望
和安全错误码；仍要求 FAILED、零工具和零产物。旧 1.2.0 baseline 单独保留，1.2.1
重新建立同版本门槛，不把口径变化称为性能提升。V01–V20 冻结集及本轮对比口径不变。
