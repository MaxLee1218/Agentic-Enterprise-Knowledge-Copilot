# 供应商质量分析修复后回归 — 2026-10-19

四类交付缺陷修复后，相同十场景的单次真实模型回归取得 **9/9 合格报告**，
数值检查 **181/181**、枚举事实支持 **146/146**、P10 安全拒绝 **1/1**。
相比[保留的原基线](supplier-quality-pilot-result.md)，新增五份合格报告、六项数值检查和
十八项事实支持检查通过。没有未运行、待审批或未决费用；十个场景各执行一次，没有挑选复测结果。

这是已参与调试的固定合成场景上的开发回归，不是 held-out 测试、生产成功率或实测人力节省。
原始失败、原 P01 校准与两次复测均保持原样。

## 可审计的前后对比

| 观察指标 | 原轮 | 修复后 |
|---|---:|---:|
| 合格报告 | 4/9 | 9/9 |
| 独立数值检查 | 175/181 | 181/181 |
| 枚举事实支持 | 128/146 | 146/146 |
| P10 安全拒绝 | 1/1 | 1/1 |
| 全部报告任务费用 / 合格报告数，USD 上界 | 0.003927726 | 0.001482535333… |
| 合格报告耗时中位数 | 5.3715 秒，4 份 | 6.186 秒，9 份 |

两轮合格报告集合不同，不据此计算速度提升百分比。修复后耗时范围为 4.582–12.309 秒，
包含真实 PostgreSQL Worker 的脚本化澄清续跑；这些不是生产 p50/p95 或服务承诺。
每份成本以当轮全部报告任务费用（包含失败、重试）除以合格报告数，不能解读为模型单次推理变便宜。
事实支持检查只覆盖预先枚举的数值、null、无记录及政策口径，不代表任意自然语言结论均正确。

完整证据：

- [前后对比 JSON](../evaluation/reports/pilot-post-fix-comparison-20261019.json)与
  [Markdown](../evaluation/reports/pilot-post-fix-comparison-20261019.md)。
- [本轮逐项机器结果](../evaluation/reports/pilot-post-fix-live-20261019.json)、
  [场景表](../evaluation/reports/pilot-post-fix-live-20261019.md)及
  [冻结配置](../evaluation/reports/pilot-post-fix-live-20261019-config.json)。
- [四项缺陷及回归 Issue 图](supplier-quality-post-fix-regression.md#github-工作项)。

## 修复与逐场景结果

| 缺陷 / Issue | 实际修复 | 本轮验证 |
|---|---|---|
| 季度文案 / [#5](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/5) | 限制说明读取真实 quarter/year | P04 Q1、P06 Q3 均合格；JSON/PDF 回归测试通过 |
| 必需指标 / [#6](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/6) | 报告固定交付四项既有必需指标；未知指标拒绝 | P07 30/30，原缺失六项趋势全部通过 |
| 政策建议 / [#7](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/7) | 引用摘录只支持其内容；通用复核建议独立标明依据 | P08 合格，无无据 containment 建议 |
| 澄清范围 / [#8](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/8) | 保留原请求 supplier IDs，时间答案不扩大范围 | P09 同一 Task 续跑，仅 S-100，合格 |

P01、P02、P03、P05 保持合格。P09 的固定回答仍为 `Q2 2026`，不是一次真人交互测试。
P10 以 `FAILED / LLM_SCHEMA_VALIDATION_ERROR` 结束，实际工具调用、证据和报告均为零，
冻结的三个安全断言全部通过。本轮没有观察到它进入后续策略拒绝分支，不据此声称权限路由准确率。

生产修复提交 `c48bb7b`；回归工具与补充测试提交
`482cdce79424155bc3da882de21b9f0537b8bda5`。未变更工具契约、指标公式、审批权限或异步运行权威。

## 增量费用与累计预算

新增 **21 次** HTTP attempts，输入 **22,893 tokens**、输出 **11,314 tokens**，其中输出已含
provider 所计 **7,954 reasoning tokens**。没有额外整个场景复测，也没有复用原 P01 交付物。
原 25 次 attempts 完整保留，同一持久账本累计 **46 次**，全部已对账。

| 费用口径 | USD，冻结费率上界 |
|---|---:|
| 本轮 P01–P09 全部报告任务 | 0.013342818 |
| 本轮每份合格报告，上一行 /9 | 0.001482535333… |
| 本轮 P10 安全场景 | 0.002022444 |
| 本轮全部新增费用 | 0.015365262 |
| 原实验累计费用 | 0.020836866 |
| 修复后全部实验累计费用 | 0.036202128 |

EUR 预算记账上界累计 **€0.056587**，没有未决预留；沿用原授权 €10 总额与 €2 预留，
没有开启新预算。USD 按实际 usage 乘冻结费率；EUR 包含保守汇率与税费余量，均非发票金额。
价格卡 `deepseek-flash-peak-2026-10-19.v1`、原预算及账本身份保持不变。

## 版本、样例与可复现范围

本轮 UTC 窗口为 `2026-10-19T10:00:18.629987Z` 至 `10:01:27.555382Z`，执行前工作区干净。
代码版本为 `482cdce79424155bc3da882de21b9f0537b8bda5`，代码 hash 为
`sha256:8527a027bb340ff631e6d8403dc2f38a747dc1a4687c626bba99746f5a2e6c05`。
模型仍为 `deepseek-flash`，提示词仍为 `task-understanding-v4` 与 `planner-v3`；变更位于确定性代码。
数据、政策、固定分母均与原轮相同；配置 hash 因 population、代码及原基线绑定发生变化，详见配置文件。
原轮 P01 使用更早的 `096ca89`，其余首轮主要为 `3c4bf61`；两轮机器快照保留逐场景版本。

本轮绑定的原公开基线 SHA-256：
`1c0c79becbfdf3c4f112ca0d840efc79dca2d5e4c6158894a5c1a8471871ab0d`。
比较器重新计算计分、状态、费用和延迟，并核验原消费记录是当前账本的完整子集。
公开快照仅规范化交付物路径；断言、费用及 checksum 保持不变，比较 hash 绑定公开文件实际字节。

只发布两份实际合格样例，按原字节复制并核验 checksum：

- [P02 八页 PDF](../evaluation/reports/pilot-post-fix-live-20261019-sample.pdf)，全部页面已渲染检查。
- [P01 JSON](../evaluation/reports/pilot-post-fix-live-20261019-artifact.json)。

其余原交付物、运行捕获、冻结指针及消费账本留在本地实验目录。命令与恢复限制见
[评估入口](evaluation.md#explicit-post-fix-regression)。不要清空账本、删除指针或重复付费运行来替换此结果。

本地验证为 Python 3.11 全量 **966 passed、37 skipped**，独立真实 PostgreSQL 回归测试
**2 passed**；供应商与应付账款离线评估分别 **30/30、25/25**，MCP 评估 **12/12**。
Ruff、格式、Mypy（527 文件）、文档、架构及构建通过；Standards 与 Spec 双轴审查无剩余阻断项。

可用于面试的表述：

“建立企业 Agent 十场景端到端评估，以独立 SQL/Decimal 标准答案、固定分母、引用验收与逐调用
预算账本发现四类交付缺陷。修复后在相同开发场景上，合格报告由 4/9 提升至 9/9，数值检查
由 175/181 提升至 181/181，事实支持由 128/146 提升至 146/146；全部调用和原始失败均可追溯。”
