# Supplier Quality development regression v1 结果

跟踪 [#16](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/16)，
修复 [#13](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/13)、
[#14](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/14)、
[#15](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/15) 后，按
[执行前协议](supplier-quality-validation-regression-v1.md)各运行 V01–V20 一次。真实执行代码
`45885a9d420502a3a3a3810c803a903b06b82754`，git dirty=false；新 Task、新 PostgreSQL runtime
数据库、原账本，未恢复原 V16。原首次验证及其 partial 状态均保留。

## 结果与比较

| 指标 | 首次 PDF 审计基线 | 本轮独立版本回归 |
|---|---|---|
| 合格报告 | 8/16（50.0%） | **15/16（93.75%）** |
| 数值断言 | 270/317 | **287/317（90.54%）** |
| 枚举事实 | 218/256 | **232/256（90.625%）** |
| 安全拒绝 | 4/4 | **4/4** |
| partial | true，V16 等待 | **false** |
| 合格任务耗时中位数/范围 | 8.871 s / 5.541–11.524 s | **8.156 s / 5.711–19.827 s** |

首次自动成绩为 11/16，实际 PDF 审计后为 8/16；比较使用后者。本轮自动及实际 PDF 审计均为
15/16。两个版本输入、source、literal oracle、政策、固定答案、场景顺序、模型配置、分母和
`visible-quarter.v1` 审计口径一致。首次由两个 harness 版本分段执行，本轮为一个版本；不能把
差异解释为独立盲测、单一因素的因果收益或生产成功率。

原未合格的 V02/V12/V15（可见 PDF 季度）、V04/V07/V10（类型识别）、V14/V16（期间澄清）
本轮全部合格。V13–V16 都经同一 Task 的 PostgreSQL dispatcher/Worker 恢复完成，回答前
均为零工具调用。V16 原请求年份被保留，固定回答 `Q4` 足够完成本轮新 Task；原 V16 仍等待。

**本轮新增 V01 失败**：首次该场景合格，本轮在工具执行前出现
`LLM_SCHEMA_VALIDATION_ERROR` / VALIDATION / nonrecoverable，耗时 5732 ms，一次已结算
HTTP 请求。未保留 provider 原候选，不能推断具体被拒字段或将其直接归因为 #15。完整保留失败、
零工具、零产物和费用，未重跑。后续诊断跟踪
[#17](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/17)。缺失的 V01
30 个数值、24 个事实仍计入总分母，不能从分母删去。

## 费用与累计预算

| 费用口径 | 本轮 USD peak-rate 上界 |
|---|---|
| 报告任务（含 V01 失败及所有重试） | 0.035251596 |
| 安全任务 | 0.007787676 |
| 本轮总增量 | **0.043039272** |
| 每份合格报告：报告任务费 / 15 | **0.0023501064** |

43 次新 HTTP attempt 全部已结算，原 81 次记录字节未变，累计 **124 次**；累计 USD 上界
0.111207768、保守 EUR/税费上界 **0.173820**，未决 EUR **0**。授权仍是原总额 EUR10、
预留 EUR2，不是本轮另给 EUR10。价格卡、每次 token 用量/计费摘要及历史价格都在机器报告中。
这些数值是模型使用费上界，不是供应商账单，不含基础设施和人工复核。

合格耗时样本从 8 个变为 15 个，所以仅报告观察中位数/范围；不计算“提速百分比”。失败、等待及
安全场景耗时逐项见比较表。人工工作时长和节省比例仍未实测。

## 证据与复核

- [逐场景比较](../evaluation/reports/supplier-validation-regression-v1-20261020-comparison.md)及
  [比较 JSON](../evaluation/reports/supplier-validation-regression-v1-20261020-comparison.json)：
  绑定前后配置、报告 hash、分母、全部状态、失败断言、两侧耗时和完整费用口径。
- [审计后完整 JSON](../evaluation/reports/supplier-validation-regression-v1-20261020-audited.json)及
  [审计后 Markdown](../evaluation/reports/supplier-validation-regression-v1-20261020-audited.md)。
  [自动原结果](../evaluation/reports/supplier-validation-regression-v1-20261020-automated.json)单独保留。
- [配置](../evaluation/reports/supplier-validation-regression-v1-20261020-config.json)、
  [进度](../evaluation/reports/supplier-validation-regression-v1-20261020-progress.json)及
  [发布映射/校验和](../evaluation/reports/supplier-validation-regression-v1-20261020-publication.json)。
  公开报告只把 artifact_paths 转为仓库相对路径；未提交的私有产物仍保留本机，进度中的 case hash
  指向私有原件。比较 JSON 对公开视图重新计算 hash，映射同时记录私有/公开 hash。
- 两份与实际产物字节一致的合格样例：
  [V02 Q1 PDF](../evaluation/reports/supplier-validation-regression-v1-20261020-samples/V02.pdf)、
  [V16 Q4 JSON](../evaluation/reports/supplier-validation-regression-v1-20261020-samples/V16.json)。
  本轮四份实际 PDF 均通过可见季度/完整性审计；V02 的全部 8 页另经渲染视觉检查，未见文字裁切、
  表格重叠或证据溢出，实际第 2 页图题为 Q1，封面/限制为 Q1 2026，null 保留为 N/A。

执行前完整本地检查 **1015 passed、35 skipped**（跳过项目有独立环境要求）；真实本机 PostgreSQL
控制测试包含在通过项中。Ruff/format、Mypy（543 source files）、docs/architecture、actionlint、
distribution build、Supplier mock 30/30；前置 AP 25/25、MCP interoperability 13/13 和 safety 12/12
通过。遗留 30-case 开发集 1.2.1 的非法季度口径修正及旧 baseline 归档见
[修复记录](supplier-quality-validation-fixes.md)，与本 V01–V20 输入和验收无关。

### Standards

未发现剩余规范阻塞或显著 smell。新入口复用既有执行、恢复和预算边界，费用和首次结果分开保留。

### Spec

未发现剩余规格阻塞。复核发现的逐场景失败/等待耗时缺项已补齐，并先失败、后通过控制测试。
模型说明区分官方 alias 映射与逐响应指纹。

Standards 0 项、Spec 0 项剩余问题。最终发布检查与远端 CI 证据记录在 #16。
