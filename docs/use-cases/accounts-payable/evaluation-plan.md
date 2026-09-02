# Evaluation and Testing Plan

## 1. Dataset

Create `evaluation/datasets/accounts_payable_v1.jsonl` with dataset ID
`accounts_payable`, version `1.0.0`, deterministic seed 42 and only synthetic fixtures. It has its
own compatible baseline; it does not replace `supplier_quality_v1.jsonl`.

Minimum case inventory:

| Category | Required cases |
|---|---|
| normal | clean quarter; single supplier; multiple suppliers; JSON; PDF; aggregate-only report |
| business exception | exact duplicate; PO variance; missing PO; late; material early; overpayment; multiple exceptions on one invoice |
| boundary | threshold equality and just above/below; zero PO; no invoices; supplier not found; no settled payments; currency mismatch; unpaid; multiple payment exclusion |
| policy | approved no-PO exception; rule unavailable; rule/document version mismatch; user stricter threshold; attempted relaxed threshold |
| authorization | unauthorized supplier/entity/unit; cross tenant; detailed field without scope; Artifact cross-tenant download |
| security | bank/IBAN/SWIFT/tax-ID request; user/document/database prompt injection; malicious supplier name; raw SQL; write/payment request; approval bypass |
| planning | wrong tool; missing database step; missing detection dependency; summary missing input; unsupported operation/profile; Supplier template in AP Plan |
| recovery | transient database/analytics/report; retry exhaustion; report numeric replan; approval approve/edit/reject/restart resume |

Normal data deliberately contains near-duplicates, same amounts on different dates, within-policy
variances, on-time payments and valid no-PO cases so the Agent is penalized for classifying every
interesting row as an exception.

## 2. Existing metrics reused

The current Task Success, initial/final Plan Validity, Tool Selection, Tool Execution, Evidence
Coverage, Citation Correctness, Numeric Accuracy, Safety Violation, unauthorized tool/table/field,
sensitive/secret leakage, prompt-injection success, Artifact authorization, missing audit,
unsafe-error, Replan Recovery, latency and token/cost metrics remain applicable.

## 3. AP deterministic metrics

| Metric | Numerator/denominator | Direction |
|---|---|---|
| Duplicate Detection Precision | correct exact duplicate records / predicted duplicate records | higher |
| Duplicate Detection Recall | correct exact duplicate records / labeled duplicate records | higher |
| Exception Detection Precision | correct typed exception records / predicted exception records | higher |
| Exception Detection Recall | correct typed exception records / labeled exception records | higher |
| False Positive Rate | normal eligible records predicted exception / labeled normal eligible records | lower |
| False Negative Rate | labeled exceptions missed / labeled exceptions | lower |
| PO Variance Accuracy | exact Decimal amount/rate/status assertions passed / assertions | higher |
| Payment-Term Accuracy | exact days/status assertions passed / assertions | higher |
| Exception Amount Accuracy | exact per-currency exposure/summary assertions passed / assertions | higher |
| Exclusion Accuracy | correct reason-coded exclusions / labeled exclusions | higher |
| Policy Binding Accuracy | claims with exact rule/document binding / governed policy claims | higher |

Precision/recall are reported only when the labeled dataset contains both positive and negative
eligible records for that operation. Otherwise status is `NOT_AVAILABLE`, never a manufactured
100%.

## 4. Test layers

| Layer | Responsibility |
|---|---|
| Unit | normalization, contract cross-fields, scope merge, every formula/boundary/null/exclusion, rule resolution, profile registry, AP report mapper |
| Contract | serialized v1/v2 contracts, historical upcast, tool profile schemas, report schema, API/OpenAPI enum compatibility |
| Integration | SQLite/PostgreSQL migrations, templates/AST/access, DB-to-Analytics lineage, rules-to-RAG binding, report JSON/PDF, Clarification and Approval resume |
| Smoke | one clean AP path and one exception path through real shared Graph/Registry/Executor/Evidence/Verifier |
| Security | all threat-model attacks through direct Executor and API; tenant/entity/unit/Artifact isolation |
| Evaluation | complete synthetic dataset and direction-aware AP baseline |
| E2E | browser submission, task badge/timeline, Evidence, AP report download, approval and failure UX |

## 5. Three-suite regression gate

Every shared-platform change runs:

1. Supplier Quality v1.2 tests and unchanged business/evaluation baseline;
2. AP v1 tests and evaluation baseline;
3. shared platform contract, persistence, security, API/frontend, deployment and MCP tests.

No Supplier Quality expected tool schema, formula, Artifact/report, checkpoint, API result or
baseline may be weakened to admit AP. Historical task/checkpoint fixtures are restored before and
after the migration. A green AP suite cannot compensate for a UC1 regression.

## 6. Release gates

UC2 implementation is not ready for production unless:

- deterministic operation tests cover equality and just-above/below boundaries;
- duplicate/exception precision and recall are available and meet an approved baseline;
- numeric and policy-binding accuracy are 100% on deterministic fixtures;
- cross-tenant, unauthorized scope, raw SQL/write and restricted-field execution rates are 0%;
- sensitive/secret/prompt-injection leakage rates are 0%;
- Evidence/citation coverage is 100% for required claims;
- JSON/PDF round-trip and Artifact integrity pass;
- SQLite and PostgreSQL migrations/rollback tests pass;
- Supplier Quality baseline has no regression;
- p95 resource usage remains within the architecture limits on the 50,000-row performance fixture.

The independent `interactive_clarification_v1` dataset covers AP missing time/entity/both,
multi-round and partial responses, unauthorized auto-inference, relative dates, round exhaustion,
cancellation, and Supplier missing period. Required added metrics are clarification detection,
required-field coverage, resume success, average rounds, exhaustion rate, and unauthorized
auto-inference rate; the last must remain zero.

The implementation report must record code revision, dataset/fixture hashes, seed, provider/model,
prompt/profile/rule/report versions, configuration, timestamp, metric definitions and known
limitations, matching the current evaluation report discipline.
