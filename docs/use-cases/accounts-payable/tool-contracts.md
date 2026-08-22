# Tool Contracts

## 1. Reuse rule

UC2 adds no capability name. It adds versioned AP contract profiles behind the existing
`knowledge_search`, `database_query`, `analysis_engine`, and `report_generator` names. Every call
still uses the current `ToolCall` envelope, Registry, policy/approval validation, Executor,
Evidence registration and Audit. Tools cannot call one another or change Task state.

`TaskStep.tool_version` and `contract_profile` bind execution and resume. Supplier Quality v1
profiles remain resolvable by historical schema fingerprint. AP v1 profiles are:

| Capability | Tool version | Contract profile | Risk |
|---|---|---|---|
| `knowledge_search` | existing adapter-compatible v2 | `accounts_payable_policy.v1` | LOW; RESTRICTED corpus MEDIUM |
| `database_query` | adapter v2 | `accounts_payable_database.v1` | MEDIUM |
| `analysis_engine` | engine v2 | `accounts_payable_analytics.v1` | LOW |
| `report_generator` | renderer v2 | `accounts_payable_report.v1` | LOW |

## 2. Knowledge Search profile

The existing input shape is sufficient. AP uses:

```json
{
  "query": "AP, procurement, PO, invoice approval and payment-term policy for the scoped investigation",
  "tenant_id": "trusted",
  "collection_ids": ["accounts-payable-policy-v1"],
  "supplier_ids": [],
  "date_range": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "top_k": 12,
  "index_snapshot_id": "tenant-bound immutable snapshot"
}
```

The same output and DOCUMENT Evidence type are reused. Required metadata adds domain namespace,
document effective dates, document classification and policy-rule binding IDs. `top_k` remains
editable only downward. A required policy/rule binding with no matching exact document version is
a non-retryable business failure, not permission to use a model interpretation.

Four controlled document families are sufficient:

| Document | Business purpose |
|---|---|
| Accounts Payable Policy | invoice fields, duplicate handling, processing and escalation |
| Procurement and Purchase Order Policy | when PO is required, single-invoice matching and variance tolerance |
| Invoice Approval and Delegation Policy | approved no-PO exception and materiality/escalation authority |
| Payment Terms Policy | due-date basis, late payment and material early-payment threshold |

Ingestion namespace is `tenant/{tenant_id}/finance/accounts-payable/v1`. Metadata includes
`document_id`, `document_version`, effective dates, classification, owner, language,
`policy_rule_set_version`, checksum and approved collection ID. Re-indexing creates a new snapshot;
tasks keep the old snapshot/version. An ingestion command will validate and publish documents and
rule manifest atomically from the task's perspective; copying PDFs into a folder is insufficient.

## 3. Database Query profile

The planner never supplies SQL. AP input uses the existing envelope with
`schema_version="accounts_payable.v1"`, bounded parameters, and one allowlisted template:

```text
tenant_id, start_date, end_date, supplier_ids,
legal_entity_ids, business_unit_ids, currency_scope
```

| Template | Purpose | Authorized output |
|---|---|---|
| `ap_invoice_population_v1` | common eligible invoice cohort and coverage | opaque invoice key, scoped dimensions, dates, amounts/currency, PO/payment cardinality and eligibility reason |
| `ap_duplicate_invoice_candidates_v1` | deterministic exact-key input | opaque key, supplier, normalized invoice number, invoice date, gross amount, currency |
| `ap_invoice_po_variance_v1` | eligible invoice/PO pairs and missing-PO facts | invoice and PO opaque keys, amounts, currency, matching basis, exception reference |
| `ap_payment_terms_v1` | due-date and single settled-payment facts | invoice key, due date, payment date/cardinality/status, currency |
| `ap_payment_amount_v1` | overpayment inputs | invoice key, gross amount, payment amount, payment cardinality/status, currency |

The common population may be reused by analytics only when it exposes exactly the required
allowlisted columns; otherwise the dedicated template is mandatory. Query output retains current
`columns`, `rows`, `row_count`, `empty_result`, `truncated`, `query_fingerprint`, and `snapshot_at`.
DATABASE Evidence additionally records template version, schema snapshot/version, sorted physical
tables/columns, tenant/supplier/entity/unit/time/currency scope hashes, row count and dataset
checksum. It stores no raw SQL, credentials, bank fields, unrestricted payment reference, or full
financial payload.

The implementation must continue to use trusted SQLAlchemy `Select`, template/table/column/function
allowlists, bound parameters, tenant and scope predicates, read-only transaction, statement
timeout, sentinel row limit and AST validation. `row_limit` may only be edited downward.

## 4. Analytics profile

AP requests are a strict operation union:

```python
class APAnalyticsRequestV1(ContractModel):
    operation_name: APAnalyticsOperation
    operation_version: str
    datasets: tuple[DatasetReference, ...]
    rule_snapshot: PolicyRuleSnapshot
    parameters: OperationParameters
    engine_version: Literal["accounts_payable_analytics.v1"]
```

`DatasetReference` contains rows, DATABASE Evidence ID, dataset checksum and template version.
The engine verifies current Task/tenant ownership and checksum before calculation. Outputs use a
common envelope with `operation_name/version`, `records`, `metrics`, `warnings`, eligibility and
exclusion counts, rule versions, input checksums and `empty_result`. Exact schemas and formulas are
in [Analytics design](analytics-design.md).

Each call returns CALCULATION Evidence referencing every input DATABASE Evidence ID and the rule
manifest/document Evidence IDs it used. No external access, LLM calculation or arbitrary code is
allowed.

## 5. Report profile

The report input is:

```text
task_id
scope (time, supplier, legal entity, business unit, currency)
exception_summary_result
evidence_refs
policy_rule_snapshot
template_version = accounts_payable_report.v1
format = PDF | JSON
language = zh-CN | en-US
detail_access = AGGREGATE | DETAIL
```

`detail_access` comes from trusted policy and cannot be chosen by the model. The output uses the
existing Artifact metadata fields and AP Artifact types. The report tool composes only structured
analytics results, never recalculates values, and never publishes externally.

`AccountsPayableReportV1` has:

```text
title, executive_summary, scope, data_overview, applicable_policies,
exception_summary, duplicate_invoice_findings, po_compliance_findings,
payment_findings, material_exceptions, supplier_summary, risk_observations,
recommended_actions, limitations, evidence, execution_trace, execution_metadata
```

`recommended_actions` are bounded business recommendations such as manual review, policy owner
confirmation or source-data correction. They cannot approve, edit or pay a business transaction.
Both PDF and JSON derive from the same strong model. Report success remains pre-verification; the
Task reaches `COMPLETED` only after the independent Verifier passes.

## 6. Failure mapping

| Condition | Stable code | Type/retry |
|---|---|---|
| unsupported exception/operation | `UNSUPPORTED_EXCEPTION_TYPE` / `ANALYSIS_OPERATION_UNSUPPORTED` | VALIDATION/BUSINESS; no retry |
| incomplete required invoice facts | `AP_DATA_INCOMPLETE` | BUSINESS; reason-coded exclusions unless requested analysis has no eligible coverage |
| no active rule or rule/document mismatch | `POLICY_RULE_UNAVAILABLE` / `POLICY_RULE_BINDING_MISMATCH` | BUSINESS/VALIDATION; no retry |
| currency mismatch | `AP_CURRENCY_MISMATCH_EXCLUDED` | warning/exclusion; no arithmetic |
| invalid due date/term | `AP_PAYMENT_TERM_INVALID` | warning/exclusion; fail only if zero eligible coverage for requested operation |
| population truncated | `AP_SCOPE_TOO_LARGE` | BUSINESS recoverable by new narrower Task |
| forbidden table/column/raw SQL/write | existing database denial family | PERMISSION; no retry |
| transient dependency/runtime | existing `*_UNAVAILABLE`, `*_FAILURE`, `*_TIMEOUT` | bounded retry only |

New Python exception classes are added only when Tool Executor mapping, HTTP semantics or workflow
recovery differs. Otherwise these remain stable `TaskError.error_code` values in the existing
error model.

## 7. Idempotency and versions

- Database: template ID/version + canonical parameters + schema version + `snapshot_at`.
- Analytics: operation/version + dataset checksums + rule manifest checksum + canonical parameters.
- Report: report schema/template/generator + normalized summary + Evidence checksums + format +
  detail-access mode.

All versions and final approved arguments participate in the existing action fingerprint and
audit. A rule or policy snapshot change produces a new calculation and report; it never reuses a
stale result.
