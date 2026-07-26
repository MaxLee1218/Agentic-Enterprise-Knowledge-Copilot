# Implemented Tool Contracts

This document describes the currently implemented adapter behavior. The frozen Supplier Quality
v1.0 authority remains [`docs/design/tool_contract.md`](design/tool_contract.md); this file does
not broaden or replace that baseline.

## Analytics Tool: `analysis_engine`

### Purpose and governance

- Purpose: deterministic Supplier Quality calculations over a Database Tool dataset.
- Version: `1.0.0`; calculation version `quality_metrics.v1`.
- Risk: `LOW`.
- Access: no network, database, LLM, filesystem, or arbitrary Python execution.
- Approval: no separate human approval after the input database evidence has been authorized.
- Read-only and idempotent: identical dataset checksum, metric set, grouping, and engine version
  produce identical output.
- Timeout: 15 seconds per attempt and 25 seconds overall; at most two workflow attempts.
- Invocation: registered as `analysis_engine` and executed through `ToolExecutor`.

### Input

The strict input object contains:

| Field | Type | Rule |
|---|---|---|
| `dataset` | array | At most 10,000 normalized rows |
| `dataset_evidence_id` | string | Must identify DATABASE evidence owned by the current Task |
| `dataset_checksum` | string | Must match both the Evidence item and canonical dataset bytes |
| `metrics` | unique array | One or more frozen metric names |
| `group_by` | unique array | At most `supplier_id` and `period` |
| `engine_version` | string | Exactly `quality_metrics.v1` |

Each row contains non-empty `supplier_id` and `period`, plus non-negative integer
`inspected_count` and `defect_count`. `defect_count` cannot exceed `inspected_count`. Duplicate
rows are retained and summed; they are not silently deduplicated.

The only supported metrics are:

| Metric | Formula | Unit |
|---|---|---|
| `defect_count` | `sum(defect_count)` | `count` |
| `inspected_count` | `sum(inspected_count)` | `count` |
| `defect_rate` | `sum(defect_count) / sum(inspected_count)` | `ratio` |
| `period_over_period_trend` | `current_period_defect_rate - previous_period_defect_rate` | `ratio_delta` |

Groups and periods use stable lexicographic ordering. Trend values are calculated for every
series and period; the first period has no predecessor and therefore has a null value plus a
warning.

### Output and precision

The output contains `metrics`, `warnings`, `input_row_count`, `dataset_checksum`,
`calculation_version`, and `empty_result`. Every metric records dimensions, value, unit,
numerator, and denominator.

Ratios and ratio deltas are quantized to four decimal places using decimal arithmetic and
round-half-even. Count aggregation uses exact integers. NaN and infinity are rejected.

An empty dataset is a successful business result:

```json
{
  "metrics": [],
  "warnings": ["No rows were available for calculation"],
  "input_row_count": 0,
  "empty_result": true
}
```

A zero denominator produces `value=null` and a scope-specific warning. It never produces NaN,
infinity, `0%`, or a tool failure.

### Calculation Evidence

The adapter returns one `CALCULATION` `EvidenceDraft`. Its source reference records:

- operation and calculation version;
- the formula for every requested metric;
- dataset checksum and grouping;
- `input_evidence_ids=[dataset_evidence_id]`.

Its content contains the minimized metric results, warnings, input row count, empty-result flag,
classification inherited from the database evidence, and a canonical SHA-256 checksum.
`ToolExecutor` asks the Evidence Ledger to bind the authoritative Evidence ID, Task ID, Step ID,
ToolCall ID, and timestamp. The Analytics Tool does not write directly to the ledger.

### Failure semantics

| Condition | Error | Retry |
|---|---|---|
| Unsupported metric/dimension or malformed row | `ANALYSIS_INPUT_INVALID` or executor schema rejection | No |
| Missing, cross-Task, non-DATABASE, or checksum-mismatched evidence | `ANALYSIS_INPUT_DENIED` | No |
| Invalid computed numeric result | `ANALYSIS_ENGINE_FAILURE` | No |
| Executor deadline reached | `ANALYSIS_TIMEOUT` / `TOOL_TIMEOUT` | Only within the frozen retry budget |

Output Schema validation, evidence registration, audit recording, and latency measurement remain
owned by the generic governed executor lifecycle.

## Verification metadata

The Database Tool's Evidence source reference includes the frozen `query_fingerprint`, sorted
authorized table and column names, and explicit `statement_type=SELECT` / `read_only=true`
metadata. The Safety Verifier consumes those fields without parsing or executing SQL.

The Analytics Tool's structured metrics and `input_evidence_ids` are the Numeric and Citation
Verifier baselines. Verification never reruns a metric formula. Full Ledger, lineage, precision,
and issue rules are documented in
[`evidence-and-verification.md`](evidence-and-verification.md).

## Report Tool: `report_generator`

The implemented adapter follows the exact frozen `PDF | JSON` input and output schemas. It consumes
only the current Task's structured Analytics result and Document/Database/Calculation Evidence,
uses version `supplier_quality_report.v1`, and executes only through
`ToolRegistry -> ToolExecutor -> ReportTool`.

Both formats derive from `ReportDocument`. JSON uses strict stable serialization; PDF uses a local
offline renderer and carries the same structured model for independent verification. Pre-render
validation checks query fingerprints, calculation formulas, Database lineage, Analytics metric
identity, Task ownership, and finite numbers. Atomic storage verifies final SHA-256 and size before
saving frozen Artifact metadata.

The tool produces no new `EvidenceItem`: the frozen Evidence enum has no report-Artifact source,
and the frozen walkthrough records Artifact generation in audit while the Artifact cites all
upstream Evidence. See [`report-tool.md`](report-tool.md) for format, persistence, validation, and
error details.
