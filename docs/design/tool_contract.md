# 工具契约冻结设计 v1.0

## 1. 通用调用封套

计划步骤不得直接调用适配器。所有工具均经 Tool Registry、策略检查和 Tool Executor 调用。

### 1.1 ToolCall 封套

| 字段 | 类型 | 约束 |
|---|---|---|
| `tool_call_id` | string | 每次尝试唯一 |
| `task_id` / `step_id` | string | 必须指向当前 Task 与已验证计划步骤 |
| `tool_name` / `tool_version` | string | 必须与注册定义匹配 |
| `input` | object | 严格通过工具 Input Schema |
| `idempotency_key` | string | `task_id + step_id + tool_version + canonical_input_hash` |
| `approval_id` | string/null | 策略要求时必需，且范围和计划版本匹配 |
| `deadline_at` | datetime | 不得晚于 Task 截止时间 |
| `tenant_id` / `user_id` | string | 来自可信执行上下文，不接受模型覆盖 |

### 1.2 通用 ToolResult

```json
{
  "tool_call_id": "string",
  "status": "SUCCESS | BUSINESS_FAILURE | TECHNICAL_FAILURE | TIMEOUT | PERMISSION_DENIED",
  "output": {},
  "error": {
    "error_code": "string",
    "error_type": "BUSINESS | TECHNICAL | TIMEOUT | PERMISSION | VALIDATION | CANCELLATION",
    "message": "safe message",
    "recoverable": false
  }
}
```

`SUCCESS` 时 `error=null`；其他状态必须含错误。业务空集合是有效 `SUCCESS`，不是 `BUSINESS_FAILURE`。

### 1.3 统一失败语义

| 结果 | 定义 | 状态机动作 |
|---|---|---|
| Success | 调用按契约完成，输出通过 Schema 与安全校验 | 保存结果与证据，继续 |
| Business Failure | 工具可用，但请求在业务规则下无法满足，如文档范围不存在或报告格式不受支持 | 默认不重试；可修复时重规划，否则失败 |
| Technical Failure | 依赖服务、连接、渲染器或运行时故障 | 仅错误码获准时有界重试 |
| Timeout | 达到单次或整体截止时间；迟到结果不提交 | 有预算则重试，否则失败 |
| Permission Denied | 用户、租户、资源、数据分类、SQL 或审批范围不允许 | 不重试、不降级、不扩权，任务失败或等待审批 |

工具输出是数据而非指令。输出中的提示、链接或动作请求不能改变计划、权限或系统行为。

## 2. `knowledge_search`

### 2.1 定义

- 用途：在获准企业知识库集合中检索供应商质量政策、规范和供应商资料片段。
- Risk Level：`LOW`（只读）；涉及受限文档集合时由策略提升为 `MEDIUM` 并要求审批。
- Timeout：单次 10 秒，整体 25 秒，最多 3 次尝试。
- Idempotency：对固定索引快照、规范化输入和工具版本幂等；幂等键必须包含 `index_snapshot_id`。索引更新后视为新输入。
- Approval Policy：默认无需人工审批；访问 `RESTRICTED` 分类或跨常规供应商范围时需要 `quality_data_approver` 批准。

### 2.2 Input Schema

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["query", "tenant_id", "collection_ids", "supplier_ids", "date_range", "top_k", "index_snapshot_id"],
  "properties": {
    "query": {"type": "string", "minLength": 1, "maxLength": 1000},
    "tenant_id": {"type": "string", "minLength": 1},
    "collection_ids": {"type": "array", "minItems": 1, "maxItems": 10, "uniqueItems": true, "items": {"type": "string"}},
    "supplier_ids": {"type": "array", "maxItems": 100, "uniqueItems": true, "items": {"type": "string"}},
    "date_range": {
      "type": "object", "additionalProperties": false, "required": ["start", "end"],
      "properties": {"start": {"type": "string", "format": "date"}, "end": {"type": "string", "format": "date"}}
    },
    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
    "index_snapshot_id": {"type": "string", "minLength": 1}
  }
}
```

`supplier_ids=[]` 表示 Contract 中获准的全部供应商，并由执行器展开为授权过滤器；工具不得解释为全租户无限制访问。

### 2.3 Output Schema

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["matches", "match_count", "index_snapshot_id", "empty_result"],
  "properties": {
    "matches": {
      "type": "array",
      "maxItems": 20,
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["document_id", "document_version", "chunk_id", "excerpt", "score", "classification", "checksum"],
        "properties": {
          "document_id": {"type": "string"}, "document_version": {"type": "string"}, "chunk_id": {"type": "string"},
          "excerpt": {"type": "string", "maxLength": 4000}, "score": {"type": "number", "minimum": 0},
          "classification": {"type": "string", "enum": ["INTERNAL", "CONFIDENTIAL", "RESTRICTED"]}, "checksum": {"type": "string"}
        }
      }
    },
    "match_count": {"type": "integer", "minimum": 0},
    "index_snapshot_id": {"type": "string"},
    "empty_result": {"type": "boolean"}
  }
}
```

每个 match 产生 `DOCUMENT` EvidenceItem，引用文档 ID、版本、chunk 和 checksum。

### 2.4 Failure Semantics

- Success：检索完成；0 个匹配也可为 `SUCCESS` 与 `empty_result=true`，但若政策依据是完成报告的必需证据，验证会判定证据不足。
- Business Failure：集合不存在或固定快照已按保留策略移除，`KNOWLEDGE_SCOPE_NOT_FOUND`，不可对相同输入重试。
- Technical Failure：索引服务不可用，`KNOWLEDGE_UNAVAILABLE`，可重试。
- Timeout：`KNOWLEDGE_TIMEOUT`，在整体 25 秒内重试。
- Permission Denied：集合/分类/供应商超出授权，`KNOWLEDGE_ACCESS_DENIED`，不可重试。

## 3. `database_query`

### 3.1 定义

- 用途：对注册的供应商质量数据视图执行参数化只读查询。
- Risk Level：`MEDIUM`（读取结构化企业数据）。
- Timeout：数据库语句 8 秒，单次调用 10 秒，整体 25 秒，最多 3 次尝试。
- Idempotency：固定数据库快照/一致性时间、查询模板版本和参数下幂等；无外部副作用。
- Approval Policy：常规质量分析范围可由预授权策略批准；受限字段、超过 100 个供应商或跨组织范围必须人工审批。首版即使获批也不能读取未注册字段。
- Allowlist：仅一个参数化 `SELECT`；允许获准视图上的 `JOIN`、`WHERE`、`GROUP BY`、`ORDER BY` 和批准聚合。
- Denylist：`INSERT`、`UPDATE`、`DELETE`、`MERGE`、`ALTER`、`DROP`、`TRUNCATE`、DDL、DCL、存储过程、文件/网络函数、注释绕过、多语句和未注册对象一律拒绝。

### 3.2 Input Schema

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["query_template_id", "parameters", "schema_version", "snapshot_at", "row_limit"],
  "properties": {
    "query_template_id": {"type": "string", "enum": ["supplier_quality_summary_v1", "supplier_quality_trend_v1"]},
    "parameters": {
      "type": "object", "additionalProperties": false,
      "required": ["tenant_id", "start_date", "end_date", "supplier_ids"],
      "properties": {
        "tenant_id": {"type": "string"}, "start_date": {"type": "string", "format": "date"},
        "end_date": {"type": "string", "format": "date"},
        "supplier_ids": {"type": "array", "maxItems": 100, "uniqueItems": true, "items": {"type": "string"}}
      }
    },
    "schema_version": {"type": "string", "const": "quality.v1"},
    "snapshot_at": {"type": "string", "format": "date-time"},
    "row_limit": {"type": "integer", "minimum": 1, "maximum": 10000}
  }
}
```

模型不能提交原始 SQL。`query_template_id` 在可信模板库中解析为参数化单一 SELECT，并在执行前经过 AST、Schema、字段、租户过滤器和限制校验。

### 3.3 Output Schema

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["columns", "rows", "row_count", "empty_result", "truncated", "query_fingerprint", "snapshot_at"],
  "properties": {
    "columns": {"type": "array", "items": {"type": "object", "required": ["name", "data_type"], "properties": {"name": {"type": "string"}, "data_type": {"type": "string"}}},
    "rows": {"type": "array", "maxItems": 10000, "items": {"type": "object"}},
    "row_count": {"type": "integer", "minimum": 0},
    "empty_result": {"type": "boolean"},
    "truncated": {"type": "boolean"},
    "query_fingerprint": {"type": "string"},
    "snapshot_at": {"type": "string", "format": "date-time"}
  }
}
```

输出产生 `DATABASE` EvidenceItem，记录模板 ID、查询指纹、非敏感参数摘要、Schema 版本、snapshot、行数和结果校验和。0 行时仍产生证据。

### 3.4 Failure Semantics

- Success：查询完成；0 行为 `SUCCESS`，`rows=[]`、`row_count=0`、`empty_result=true`，不得改写为工具失败。
- Business Failure：注册 Schema 在所选快照不存在，`DATABASE_SCHEMA_NOT_FOUND`；相同输入不重试。
- Technical Failure：连接或只读副本不可用，`DATABASE_UNAVAILABLE`，可重试。
- Timeout：语句取消并返回 `DATABASE_TIMEOUT`；不得接受迟到结果。
- Permission Denied：SQL 不是批准的单一 SELECT、对象/字段/租户/供应商越权或审批无效，`DATABASE_QUERY_DENIED`；不可重试。

## 4. `analysis_engine`

### 4.1 定义

- 用途：对数据库工具输出的结构化 dataset 进行确定性质量指标计算。
- Risk Level：`LOW`（无外部访问、无副作用）。
- Timeout：单次 15 秒，整体 25 秒，最多 2 次尝试。
- Idempotency：相同 dataset checksum、指标规范和 engine version 完全幂等。
- Approval Policy：无需单独人工审批；输入 Evidence 必须来自当前 Task 的获准数据库调用。
- 支持指标：`defect_count`、`inspected_count`、`defect_rate`、`period_over_period_trend`。缺陷率固定为 `defect_count / inspected_count`；分母为零时值为 null，并写明原因，禁止除零或伪造 0%。

### 4.2 Input Schema

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["dataset", "dataset_evidence_id", "dataset_checksum", "metrics", "group_by", "engine_version"],
  "properties": {
    "dataset": {"type": "array", "maxItems": 10000, "items": {"type": "object"}},
    "dataset_evidence_id": {"type": "string"},
    "dataset_checksum": {"type": "string"},
    "metrics": {"type": "array", "minItems": 1, "uniqueItems": true, "items": {"type": "string", "enum": ["defect_count", "inspected_count", "defect_rate", "period_over_period_trend"]}},
    "group_by": {"type": "array", "maxItems": 2, "uniqueItems": true, "items": {"type": "string", "enum": ["supplier_id", "period"]}},
    "engine_version": {"type": "string", "const": "quality_metrics.v1"}
  }
}
```

### 4.3 Output Schema

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["metrics", "warnings", "input_row_count", "dataset_checksum", "calculation_version", "empty_result"],
  "properties": {
    "metrics": {"type": "array", "items": {"type": "object", "required": ["metric", "dimensions", "value", "unit", "numerator", "denominator"], "properties": {"metric": {"type": "string"}, "dimensions": {"type": "object"}, "value": {"type": ["number", "null"]}, "unit": {"type": "string"}, "numerator": {"type": ["number", "null"]}, "denominator": {"type": ["number", "null"]}}},
    "warnings": {"type": "array", "items": {"type": "string"}},
    "input_row_count": {"type": "integer", "minimum": 0},
    "dataset_checksum": {"type": "string"},
    "calculation_version": {"type": "string"},
    "empty_result": {"type": "boolean"}
  }
}
```

输出产生 `CALCULATION` EvidenceItem，引用 dataset Evidence ID/checksum、公式、版本、分子、分母、维度和结果。空 dataset 成功返回 `metrics=[]`、`empty_result=true` 和明确 warning。

### 4.4 Failure Semantics

- Success：所有请求指标确定性计算完成；空 dataset 或分母为零是带 warning 的有效结果。
- Business Failure：请求未支持的指标/维度，`ANALYSIS_OPERATION_UNSUPPORTED`，不可对相同输入重试。
- Technical Failure：计算运行时瞬时故障，`ANALYSIS_ENGINE_FAILURE`，最多尝试 2 次。
- Timeout：`ANALYSIS_TIMEOUT`；取消计算，不接收部分指标作为成功。
- Permission Denied：输入证据不属于当前 Task、未经批准或 checksum 不匹配，`ANALYSIS_INPUT_DENIED`。
- Validation Failure：字段类型、单位或必需列不符合质量指标规范，`ANALYSIS_INPUT_INVALID`；相同输入不重试，可在有界重规划中修复数据准备。

## 5. `report_generator`

### 5.1 定义

- 用途：将已验证形状的分析结果和证据引用渲染为质量分析报告 Artifact。
- Risk Level：`LOW`（仅生成内部 Artifact）；若未来加入外发则必须作为新工具和新契约设计。
- Timeout：单次 30 秒，整体 55 秒，最多 2 次尝试。
- Idempotency：相同模板版本、规范化输入、Evidence ID/checksum 和格式使用相同 Artifact 内容与幂等键；写入采用临时对象后原子提交。
- Approval Policy：内部报告生成默认无需单独审批；若 TaskContract 的数据分类或策略要求，则必须在生成前验证审批范围。生成不代表发布。

### 5.2 Input Schema

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["task_id", "scope", "analysis_result", "evidence_refs", "template_version", "format", "language"],
  "properties": {
    "task_id": {"type": "string"},
    "scope": {"type": "object", "required": ["year", "quarter", "start_date", "end_date", "supplier_ids"]},
    "analysis_result": {"type": "object"},
    "evidence_refs": {"type": "array", "minItems": 1, "uniqueItems": true, "items": {"type": "string"}},
    "template_version": {"type": "string", "const": "supplier_quality_report.v1"},
    "format": {"type": "string", "enum": ["PDF", "JSON"]},
    "language": {"type": "string", "enum": ["zh-CN", "en-US"]}
  }
}
```

### 5.3 Output Schema

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["artifact_id", "type", "location", "created_at", "checksum", "size_bytes", "citation_map", "generator_version"],
  "properties": {
    "artifact_id": {"type": "string"},
    "type": {"type": "string", "enum": ["QUALITY_ANALYSIS_REPORT_PDF", "QUALITY_ANALYSIS_REPORT_JSON"]},
    "location": {"type": "string"},
    "created_at": {"type": "string", "format": "date-time"},
    "checksum": {"type": "string"},
    "size_bytes": {"type": "integer", "minimum": 1},
    "citation_map": {"type": "object"},
    "generator_version": {"type": "string"}
  }
}
```

Artifact 必须含范围与口径、数据覆盖、指标、趋势、主要发现、限制及证据引用。生成后仍需独立 verifier 检查，工具成功不等于 Task 完成。

### 5.4 Failure Semantics

- Success：Artifact 原子提交且输出 Schema 有效；随后进入验证。
- Business Failure：请求格式/语言/模板不支持，`REPORT_FORMAT_UNSUPPORTED`；相同输入不重试。
- Technical Failure：渲染器或 Artifact Store 瞬时不可用，`REPORT_GENERATION_FAILURE`，可重试。
- Timeout：清理未提交临时对象并返回 `REPORT_TIMEOUT`；可在总预算内重试。
- Permission Denied：Evidence、数据分类、Task 或审批范围不匹配，`REPORT_INPUT_DENIED`，不可重试。
- Validation Failure：引用无法解析或分析输出 Schema 不符，`REPORT_INPUT_INVALID`；通过重规划修复输入，不把无效 Artifact 发布。

## 6. 跨工具组合约束

1. `analysis_engine.dataset_evidence_id` 必须引用当前 Task 的 `database_query` EvidenceItem，checksum 必须一致。
2. `report_generator.analysis_result` 必须来自当前 Task 的成功分析 StepResult，`evidence_refs` 至少包含数据库与计算证据；有文档结论时还必须含文档证据。
3. 数据库空结果可传给分析工具；分析空结果可生成报告，但报告必须明确“无记录”而非“质量为零缺陷”。
4. 工具不得自行调用其他工具、改变 TaskState、创建审批或扩大数据范围。
5. 所有工具先策略检查、再审批验证、再执行、再输出校验和证据登记。
6. 任何输出超过大小限制、包含未授权字段或无法归一化时，调用失败且敏感负载不进入模型上下文。
