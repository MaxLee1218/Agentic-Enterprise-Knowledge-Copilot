# Supplier Quality 修复后回归方案 v1

用户明确要求跟踪和修复四类交付缺陷，再对比原始十场景基线。本轮属于已见场景上的开发回归，
不能作为新独立测试集或生产成功率。原始 [4/9 基线](supplier-quality-pilot-result.md)及所有失败、
两次复测、样例和消费记录均保留；本轮不能替换其首次结果。

## GitHub 工作项

| Issue | 交付与验证 |
|---|---|
| [#5](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/5) | Q1/Q3 JSON/PDF 限制文案使用实际期间，继续说明跨季度比较不可用 |
| [#6](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/6) | 显式计数/比率请求仍交付报告所需四指标，未知指标不静默丢弃 |
| [#7](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/7) | 政策摘录引用与通用建议分开，不由存在 DOCUMENT 推导整改要求 |
| [#8](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/8) | 时间澄清保持原供应商范围，真实 Worker 同 Task 恢复，范围变更安全拒绝 |
| [#9](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/9) | 依赖 #5–#8 的单独留档、同账本修复后回归与完整对比 |

四项缺陷不互相阻塞；#9 使用 GitHub 原生 blocking 关系等待四项修复。
修复仅落实现有冻结 v1.3 语义，不改变工具、指标公式、授权、审批或异步运行架构。
测试沿既有 Task Understanding／Task Service、真实报告组合／PDF、独立 Artifact 验收、
PostgreSQL 澄清 Worker 和消费账本公开边界进行。

## 冻结回归口径

使用与原轮完全相同的十个请求、合成源数据与政策快照，以及不变的独立评估器。
每个 P01–P10 在修复版代码下执行一次；P01 重新调用模型并计费，不复用旧校准报告。
这轮不添加重复运行、根据结果重试整个场景或选择最佳样本；工具／模型已有有界重试照常计入。
交付分母仍为 P01–P09 /9、数值 /181、枚举事实 /146，P10 拒绝单列 /1。
失败、等待、未运行全部保留；partial 不包装成完整回归。

每个结果记录 population=`post-fix`、原基线文件 SHA-256、代码/配置/数据/政策 hash、
版本、实际 usage、耗时、原始断言与 Artifact checksum。对比读取器重新核验计分、分母、状态、
耗时和账本导出的费用；拒绝被篡改或不同输入的快照。
实际模型与提示词版本在报告中列出；任何配置差异都披露，不能静默混成同条件观测。

## 费用授权和恢复

本次明确的“再跑回归”请求授权一个 `post-fix-v1` population，继续沿用原预算：
所有原始及新增模型调用合计 €10，其中 €2 预留；不是新增 €10。
同一个持久 `budget.sqlite3` 继续在每 HTTP attempt 前做最大费用预留并对账。
原校准费用、首轮失败和复测费用都留在 aggregate 中。新增回归费用通过 attempt ID 集合差单列；
每份合格报告成本只用这轮全部报告任务费用（含失败／重试），P10 另列并计入累计预算。

原 `.suite.json` 指针不变。新固定 `.post-fix-v1.json` 指针绑定输出目录和配置／基线 hash，
不允许换目录启动第二个回归 population。未结束的场景、未知费用、账本停止或输入变化均阻止
盲目重放。`--resume` 只接受相同代码、配置、原目录和完整缓存 hash；不得删指针或重置账本。
费用不足时保留 partial 和未运行列表。价格卡过期／配置变化需要重新核实与审查，不能改日期绕过。

运行步骤见[评估入口](evaluation.md#explicit-post-fix-regression)。
结果出来前不填写目标成功率，不以估算人工分钟和系统秒数计算人力节省率。
