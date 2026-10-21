# Supplier Quality validation regression v1 协议

跟踪 [#16](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/16)，前置修复为
[#13](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/13)、
[#14](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/14)、
[#15](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/15)。用户已授权一轮独立
版本的回归和 README 首页展示，沿用原累计预算。此文是执行前协议，不是新成绩。

## 比较口径

基线为提交 `221c254bb9dfc0520631cc65824e4dcbe0f3bfd2` 的 PDF 审计结果：8/16 合格报告、
270/317 数值、218/256 枚举事实、安全拒绝 4/4，V16 等待导致 partial。原自动 11/16、中止快照、
两段 harness provenance 和原始 Artifact 永久保留。

[回归清单](../evaluation/configs/supplier-quality-validation/regression-v1.json) 在付费执行前提交，
绑定基线文件 SHA-256/规范 hash、freeze hash、已有 81 条 attempt 原记录字节摘要和固定场景顺序。
使用同一冻结 V01–V20 输入、source、literal oracle、政策、预置答案和分母；使用与基线相同的
`visible-quarter.v1` 检查实际 PDF 文本/校验和。生产仅允许 #13–#15 已复核的修复，不因实跑结果
临时改变输入、提示词、答案或验收。模型与输出/温度/重试条件保持相同，记录实际模型和提示词版本。

每个场景按序一次；有界内部 provider/repair 尝试全部计费。V13–V16 只使用原先固定的一条回答，
不能追加答案以提高成绩。新建 Task/独立 runtime 数据库，不恢复或替换原 V16。结果中所有失败、
等待、未运行、费用未知及捕获故障均保留。因预算/计费/执行器故障停止时保留 partial，不盲目重试。

## 执行和预算边界

执行沿真实 Task Service/Graph/Registry/Executor/Evidence/Verifier，脚本澄清复用现有
PostgreSQL dispatcher/Worker/lease/fencing。评估器的 oracle、预期范围和断言只用于验收，不能
进入业务输入。回归使用新的固定 population 名称和输出绑定；换目录、配置或 baseline 不能重开。
已完成缓存只读取，已开始但缺少完整结果的场景不能被自动重放。

唯一消费账本仍为 `evaluation/reports/supplier-quality-pilot/budget.sqlite3`。核对最早 46 条和本轮
基线全部 81 条原始记录，保留历史费用、价格和所有已有 population/pointer。总授权 EUR10、EUR2
预留以及逐 HTTP attempt 的可信上界检查保持；本轮不是新的 EUR10 预算。付费前核对当日官方
模型、单价、token 上限与 FX，过期价格卡或未决金额停止 dispatch。业务访问授权不由预算授权替代。

修复需通过独立控制数据的红/绿测试、完整静态/测试/离线评估门槛、Standards/Spec 复核并提交；
回归入口还需用 mock HTTP provider 验证父基线/配置/费用不变、重复 population 被拒绝、输入差异
在 dispatch 前被拒绝。CI 不依赖真实模型或凭据。

## 发布与解释

发布原始自动结果与单独 PDF 审计视图、逐断言 JSON、配置/协议/hash、逐场景前后差异和最多两份
真实合格样例。保留本轮报告任务费（含失败/重试）、安全费、增量费及原累计费用；单份成本除以
实际合格数，费用未知或零合格时不可用。延迟只报中位数/范围及等待/失败耗时，前后合格样本构成
可能变化，不发布速度提升百分比。

此集在用于定位修复后属于开发回归，不能称为独立盲测、生产可靠性、因果试验或实测人工节省。
相同输入/验收口径的前后结果可比较，但不与原十场景开发集拼接分母。README 保持现有 banner、
badges、英文介绍、导航和截图风格，在第一部分展示有证据链接的结果及基线对照；新结果未产生前
不得填入预期成绩。

## 可复现入口与执行前记录

显式入口（默认命令仍只验证 freeze，不执行 Task/模型）：

```bash
python -m evaluation.supplier_quality_validation --execute-regression \
  --output evaluation/reports/supplier-quality-pilot/validation-regression-v1 \
  --pricing evaluation/configs/supplier-quality-pilot/pricing-2026-10-20.json \
  --previous-pricing evaluation/configs/supplier-quality-pilot/pricing-2026-10-19.json \
  --budget evaluation/configs/supplier-quality-pilot/budget-v1.json
```

运行环境须设置专用已迁移的 `VALIDATION_POSTGRES_URL`；凭据从既有批准配置读取，不进入报告。
模型仍为 `deepseek-flash`、8192 输出上限、温度参数 0、provider 最多 2 次重试、transport 0 次重试、
60 秒读取超时。2026-10-20 19:27 UTC 只读复核官方
[价格/模型](https://api-docs.deepseek.com/quick_start/pricing/)、
[调用/token 契约](https://api-docs.deepseek.com/api/create-chat-completion/)、
[thinking 模式](https://api-docs.deepseek.com/guides/thinking_mode/)与
[ECB USD 参考汇率](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/eurofxref-graph-usd.en.html)，
当日既有价格卡仍有效，官方映射版本为 DeepSeek-V4.1-Flash-1017。调用/配置记录使用
`deepseek-flash` alias；官方映射说明不是逐响应模型指纹。
沿用 peak 单价、原 EUR 换算/税费保守上界，不用当时较低时段价提高预算余量。thinking 默认开启时
温度参数不生效；前后均相同设置，不将参数为零称为模型确定性。

控制测试仅使用旧 P03 synthetic fixture 与 fake HTTP，真实账本未被控制测试打开。已验证相同
基线费用前缀、失败费用、固定分母、零合格时成本不可用、缓存零新请求、换目录拒绝，以及输入、
oracle、模型、基线字节和被替换账本在 dispatch 前拒绝。基线 PDF 与回归 PDF 使用相同审计函数。

执行前门槛：完整本地 **1015 passed、35 skipped**，新比较耗时字段的控制测试另行通过；
Ruff/format、Mypy（543 source files）、docs/architecture、actionlint、distribution build 与
Supplier mock 同版本 **30/30** 均通过。前置 AP 25/25、MCP interoperability 13/13 与 safety
12/12 保持。Standards 复核无剩余项；Spec 复核发现的失败/等待耗时缺项已补齐并通过红/绿控制。
真实回归尚未开始，最终远端 CI 与实际结果将另行发布。
