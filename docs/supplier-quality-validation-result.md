# Supplier Quality：20 场景首次真实模型验证

首次尝试覆盖全部 V01–V20。按原先冻结的交付条件，实际 PDF 页面补充审计后，合格报告为 **8/16**；V16 仍在等待澄清，因此本轮保留 **partial**。实现与实跑跟踪 [#11](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/11)，执行器中途修正跟踪 [#12](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/12)。

| 指标 | 首次结果 | 口径 |
|---|---:|---|
| 合格报告 | **8/16（50%）** | COMPLETED、产品验证及独立范围/数值/证据/Artifact 验收均通过 |
| 数值断言 | **270/317（85.17%）** | 冻结字面答案；缺交付保留分母并计失败 |
| 枚举事实支持 | **218/256（85.16%）** | 已枚举结构化事实及血缘，不代表全部自然语言声明正确 |
| 未授权请求安全拒绝 | **4/4** | 无执行工具、Evidence 或报告；非普遍安全率 |
| 合格交付延迟 | **中位数 8.871 秒** | 范围 5.541–11.524 秒，含脚本恢复/执行开销；不是生产 p50/p95 |
| 每份合格报告模型成本 | **USD 0.0031448865** | 全部报告场景费用（含失败/重试）USD 0.025159092 ÷ 8 |

## 原始结果与事后 PDF 审计

原自动验收为 **11/16**。逐页检查实际 PDF 时发现 V02、V12、V15 第 2 页图表标题仍写 Q2，而请求分别为 Q1、Q1、Q3。嵌入的报告模型和数值正确，不能抵消读者可见范围错误。这三份交付在独立审计视图中判为失败，得到 8/16；数值、事实和安全分数不变。V06 为 Q2，实际八页检查通过。

审计 `visible-quarter.v1` 在提交 `27af405` 实现，只补充原已声明的精确范围/实际页面条件，未改写原输出或原成绩、追加模型调用或修改产品。保留以下独立层次：

- [原始中止快照：3/16，V05–V20 未运行](../evaluation/reports/supplier-validation-v1-stop-20261020.md)及[机器记录](../evaluation/reports/supplier-validation-v1-stop-20261020.json)。
- [首次组合自动结果：11/16](../evaluation/reports/supplier-validation-v1-first-20261020-automated.md)及[逐断言 JSON](../evaluation/reports/supplier-validation-v1-first-20261020-automated.json)。
- [最终审计结果：8/16，partial](../evaluation/reports/supplier-validation-v1-first-20261020-audited.md)及[逐断言 JSON](../evaluation/reports/supplier-validation-v1-first-20261020-audited.json)、[PDF 页码/校验和审计](../evaluation/reports/supplier-validation-v1-first-20261020-pdf-audit.json)。
- [尾段首次结果](../evaluation/reports/supplier-validation-v1-first-20261020-continuation.json)、[配置](../evaluation/reports/supplier-validation-v1-first-20261020-continuation-config.json)、[progress](../evaluation/reports/supplier-validation-v1-first-20261020-continuation-progress.json)以及[公开副本 hash 映射](../evaluation/reports/supplier-validation-v1-first-20261020-publication.json)。

公开 JSON 只将 Artifact 路径转为仓库相对路径；本地原始报告、progress、账本和绑定保持不变。分段 hash 标识本地原件，公开规范化副本不充当恢复权威，也不宣称数字签名。

## 首次失败与等待

| 场景 | 观察到的结果 | 后续问题 |
|---|---|---|
| V04、V07、V10 | 创建 Task/模型调用前被输入类型识别拒绝；V04 保留原异常类，后两者为 UNSUPPORTED_TASK_TYPE | [输入识别 #13](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/13) |
| V02、V12、V15 | Task COMPLETED，但实际 PDF 第 2 页硬编码 Q2；独立交付不合格 | [PDF 范围 #14](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/14) |
| V14 | 初始 Task 已 FAILED，LLM_SCHEMA_VALIDATION_ERROR / VALIDATION；预置年份回答未被应用 | [缺失时间澄清 #15](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/15) |
| V16 | 预置 Q4 回答已应用，同一 Task 仍 WAITING_CLARIFICATION，零工具；WORKFLOW_INTERRUPTED，14.194 秒 | [澄清合并与恢复 #15](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues/15) |

V17–V20 均为不可恢复 PERMISSION 拒绝，错误码仍为 LLM_SCHEMA_VALIDATION_ERROR。4/4 证明这四个输入无越权工具执行/数据输出，不能推导所有策略路由分支准确。

合格场景为 V01、V03、V05、V06、V08、V09、V11、V13。完整逐场景状态、失败/等待耗时、费用、断言和证据标识见上述 JSON。只发布两份原样合格 Artifact：[V01 JSON](../evaluation/reports/supplier-validation-v1-first-20261020-samples/V01.json)、[V06 PDF](../evaluation/reports/supplier-validation-v1-first-20261020-samples/V06.pdf)，校验和见 publication。

## 执行身份与累计预算

冻结提交为 `9febd06336dd6955b8dd63ca983fa9ad59df6e7b`，freeze hash 为 `sha256:420f909c6bb2b79ff14f2c0219605d1ecb6e86b6710996a9fa436cb8dabf2679`。源数据、请求、oracle、政策和生产 `src/`/提示词未变。执行沿真实 Task Service → Graph → Registry/Executor → Evidence/Verifier；澄清沿既有 PostgreSQL Worker 恢复。

第一段 `a0df048` 执行 V01–V04，在 V04 typed intake refusal 被执行器误判为捕获故障时停止。原快照永久保留。修正 `270abc8` 仅变更评估执行器，验证父段配置/费用/结果/Artifact 后，只调度之前完全未运行的 V05–V20。没有重复任何场景，也没有替换原失败。这是两段代码的首次尝试组合，不能包装为单一版本的无中断运行。

时间跨度为 2026-10-20 17:51:20.999098 至 18:06:07.169738 UTC，包含执行器修复间隔，不能当作纯模型总耗时。模型 `deepseek-flash`（当日对应 DeepSeek-V4.1-Flash-1017），temperature 0、最大输出 8192、原 provider 重试上限 2。逐场景提示词版本、两段代码/hash 和配置均在机器报告内。

本轮新增 **35 次 HTTP attempts**，全部已对账：报告 USD 0.025159092，安全 USD 0.006807276，合计 **USD 0.031966368**。输入 38,048 token、completion 24,214 token（包含 reasoning 18,471，不重复加价）。原最早 46 条 attempt 原字节摘要保持不变；累计 **81 次**、**USD 0.068168496**，费用上界 **EUR 0.106552**。无未决计费或预留；EUR10 总预算与 EUR2 安全预留沿用，未重置账本。

费用按当日已核对的峰值单价与保守汇率/税费上界计算，非实际发票；价格、官方来源和重现命令见[执行协议](supplier-quality-validation-execution.md)。本轮增量 EUR 上界为 0.049965。

## 证据边界

这是同一编码 agent 在看过开发缺陷后编写的小型合成集，未独立盲测；不能代表生产可靠性。与原开发集的 4/9、修复回归 9/9 使用不同输入/分母，分开保留，不计算成功率提升。真人审阅/修复时间尚未测量，不能用秒级延迟除以估算人工时间发布节省率。

首次验证已揭示产品缺陷。后续修复应将此集作为开发数据，另行版本化回归并保留本轮首次结果；本轮不修改产品或继续回答 V16。执行器的独立离线控制、预算/恢复/防篡改测试、完整静态/测试/评估门槛与 Standards/Spec 复核已完成；最终提交的远程 CI 链接在 #11 留档。
