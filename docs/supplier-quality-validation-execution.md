# Supplier Quality validation v1 执行协议

实现与首次实跑跟踪：[GitHub #11](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/11)。
输入与验收权威见[冻结集说明](supplier-quality-validation-v1.md)。冻结提交
`9febd06336dd6955b8dd63ca983fa9ad59df6e7b` 的六个文件保持逐字节不变。
执行前要求干净提交，且生产代码/提示词相对冻结提交未修改。

## 运行入口

默认命令仍只读核验冻结集，不读取 `.env`、不创建 Task、不调用模型：

```bash
python -m evaluation.supplier_quality_validation
```

离线测试使用原开发集的不同控制数据，不预跑 V01–V20。执行器在 source-only / raw request 边界
复用 `capture_task`；字面 oracle、预期范围/结果不会进入该边界。可信身份始终授权 S-100/S-200，
不从测试答案设置权限。控制用 mock HTTP provider 走现有 DeepSeek adapter 和逐请求预算守卫。

实跑需要专用、已迁移的 PostgreSQL Task/Queue/Checkpoint 数据库，设置 `VALIDATION_POSTGRES_URL`。
凭据继续来自批准的 `.env`/环境，只注入现有模型适配器。首次执行命令：

```bash
env LLM_PROVIDER=deepseek LLM_MODEL=deepseek-flash LLM_MAX_OUTPUT_TOKENS=8192 \
  LLM_TEMPERATURE=0 LLM_BASE_URL=https://api.deepseek.com \
  python -m evaluation.supplier_quality_validation --execute-live \
  --output evaluation/reports/supplier-quality-pilot/validation-v1 \
  --pricing evaluation/configs/supplier-quality-pilot/pricing-2026-10-20.json \
  --previous-pricing evaluation/configs/supplier-quality-pilot/pricing-2026-10-19.json \
  --budget evaluation/configs/supplier-quality-pilot/budget-v1.json
```

CLI 固定共用原 `evaluation/reports/supplier-quality-pilot/budget.sqlite3`，且核验最早 46 次调用原始
记录字节的 SHA-256。不提供账本切换参数。每次 HTTP attempt 先持久化预留再发送；所有重试/
repair 均计入。业务审批继续由产品决定，API 花费授权不能替代数据权限或业务审批。
返回码 0=完整且 16/16+4/4 合格，1=完整但存在质量/安全失败，2=停止/partial/配置不成立。

## 价格与累计预算

2026-10-20 核对 [DeepSeek 官方价格](https://api-docs.deepseek.com/quick_start/pricing/)
与[Chat Completions 上限](https://api-docs.deepseek.com/api/create-chat-completion/)：
`deepseek-flash` 当前对应 `DeepSeek-V4.1-Flash-1017`。沿用峰值计价上界
USD0.30/M cache-miss 输入、USD0.006/M cache-hit 输入、USD1.20/M 输出；不因非高峰下调。
1M 上下文按 1,048,576 输入 token 作保守上界，单次输出显式限制 8,192；completion usage
包含 reasoning 子计数，不重复加价。整个上下文按 cache-miss 加输出预留，单次最多 EUR0.506880。
未启用 provider 内置搜索/fetch/tools。

[ECB 2026-10-20 参考](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/eurofxref-graph-usd.en.html)
为 EUR1=USD1.1215；账本仍以 EUR1.25/USD 并加 25% 税费余量核算。
这是运营费用上界，不是汇率预测或实际发票税率。新卡限期至 2026-10-21 00:00 UTC，过期即停止。

评估账本 migration 002 只新增历史价格表。保留 v1 config hash、每个历史 attempt 原字节、
原费用和停止状态。首次续用必须提供与原 config hash 匹配的旧卡及同一 BudgetPolicy；旧账单按
旧卡校验，新请求按新卡计价。总授权仍 EUR10，EUR2 预留不动，未决金额继续占用预算且阻止新请求。
机器导出的 v2 spending 保存完整价格历史，并按各 attempt 的价格 hash/usage 重新校验费用。
旧 v1 报告保持兼容，未改写旧成绩。

## 一次性执行与恢复

原账本元数据绑定唯一 population/config/output，换目录不能启动另一轮。`progress.json` 在发送
任何新场景前原子写入 started/run ID 并 fsync。完成记录带 hash；报告、Artifact 或配置变更会阻止
恢复。已完成场景只读取，不重新执行。已 started 但未完成的场景标记 interrupted，保留费用与
未决预留，停止后续 dispatch，需人工核对，不能盲目重试。账本绑定本身不具有 Task/Worker 权威。

V01–V20 按序各一次。澄清沿既有接口、PostgreSQL dispatcher/Worker 和同一 Task 恢复；直接
任务仍由 Task Service 执行。控制测试不创建第二套 lease 或绕过 Registry/Executor。业务失败
保留为该场景首次结果，继续后续场景；预算/不明计费/执行器故障则停止并保存 partial/unrun。

数值及枚举事实使用冻结字面值、操作数、单位与断言 ID；季度检查汇总实际报告月度计数。
独立检查 Task/产品验证、精确范围、Evidence 血缘、政策身份及支持边界、Artifact checksum，PDF
额外检查真实页面文本与可见数值/引用。缺失交付仍保留固定 16/4/317/256 分母。
完整度和质量分开：完整执行后存在业务/质量失败不等于 partial；停止或未决计费属于 partial。

每个场景保存状态、Task/Artifact 身份、错误类型/码、逐断言结果、scripted interaction、提交至
停止延迟及全部 HTTP attempt。汇总记录提交/代码 hash、冻结身份、模型/提示词/配置/价格版本，
费用区分本轮报告任务（含失败/重试）、安全任务和原累计。单份成本用报告任务总费除以合格数；
无合格或费用不明时不可用。延迟只报样本中位数/范围及失败/等待时间。

这是同一编码 agent 编写的小型合成验证，不是独立盲测、生产成功率或实测人工节省。
本集分母/数据不同于开发集，分开报告，不合并或直接计算成功率提升。首轮后据此修改产品，
该集即转为开发数据；原首轮结果必须保留，修复回归另行版本化。


## 输入拒绝与一次性尾段续行

首次段在 `a0df048` 上运行，V04 被现有任务类型识别在创建 Task/调用模型前拒绝。
当时执行器把 `TaskIntakeValidationError` 当作捕获故障停止，原报告为 3/16、75/317、60/256、
安全 0/4，V05–V20 未运行。本地停止报告、配置、progress、原 population binding 和已计费用
保持原字节，原失败不能被下一次成功替换。跟踪修正见 [#12](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/12)。

修正只涉及评估执行器：typed intake refusal 是不合格场景，记录安全的产品错误码；未创建
Task，不能伪造 FAILED Task 或安全拒绝。真正 IO/运行时捕获故障仍停止。生产分类规则、
模型提示词、请求和标准答案均保持不变，因此 V04 继续失败。

只允许 hash 绑定的未运行尾段续行：先核对原账本 binding、原报告/config/progress hash、
逐场景结果/Artifact 和所有原费用；必须为纯 intake stop、无 Task/付费/工具的最后已启动场景，
其后均为 unrun。续行配置绑定 parent_report_hash，另存输出和唯一 continuation binding，
不清除或修改原绑定/stop，不调度 V01–V04。已开始或可能计费的中断仍不能用此入口重试。

在相同模型/价格/预算设置下，将首次命令的输出改为：

```text
--output evaluation/reports/supplier-quality-pilot/validation-v1-continuation
--continue-unrun-from evaluation/reports/supplier-quality-pilot/validation-v1
```

组合报告逐场景保留首次结果，披露两段代码/config/hash，仍使用完整固定分母及原累计账本。
只在组合视图将原 typed intake exception 归类为 intake_rejected；异常类、失败断言和原停止快照
保留，不反推伪造原错误码。不将这次分段执行包装成单一执行器版本的无中断测试。

公开停止报告仅将 Artifact 路径转为仓库相对路径；`supplier-validation-v1-stop-20261020-publication.json`
披露原始/公开 hash 的关系。本地原始数据仍用于父段绑定，公开脱敏副本不替代原账本或恢复权威。
