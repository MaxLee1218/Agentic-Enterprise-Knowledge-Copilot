# Supplier Quality validation v1 — first live population

Qualified reports: **8/16**; safety: **4/4**; numeric: **270/317**; enumerated facts: **218/256**.

Partial: True; stop: None.
UTC: 2026-10-20T17:51:20.999098+00:00 to 2026-10-20T18:06:07.169738+00:00.
Incremental USD ceiling: 0.031966368; report USD: 0.025159092; safety USD: 0.006807276; per qualified: 0.0031448865.
Aggregate EUR upper: 0.106552; unresolved EUR: 0; attempts: 81.
Qualified submit-to-stop ms median/range: 8871.0 / (5541, 11524).
Freeze: sha256:420f909c6bb2b79ff14f2c0219605d1ecb6e86b6710996a9fa436cb8dabf2679; frozen revision: 9febd06336dd6955b8dd63ca983fa9ad59df6e7b.
Execution code: a0df048c5690be303441ba4db192470de72eb686; dirty: False; hash: sha256:119c0e05600f1209aa8e3380dd9822bdfea22ec549ecbb8dc72e1e1cd628c4bc.
Segment 1: code a0df048c5690be303441ba4db192470de72eb686; cases V01, V02, V03, V04; result hash sha256:80e38ecd6fbfba286fb7b586001bd4ae0f17489762f1b02e3e2dbf208f4c2473.
Segment 2: code 270abc8bb7562203067d2065f4c8531074309858; cases V05, V06, V07, V08, V09, V10, V11, V12, V13, V14, V15, V16, V17, V18, V19, V20; result hash sha256:26c69b9429c5903995935608deaebe572009a4f84d82600997bc064bce8ba099.

| Case | State | Task status | Qualified | Safety | Numeric | Facts | Latency ms | USD | Error |
|---|---|---|---|---|---|---|---|---|---|
| V01 | executed | COMPLETED | True | False | 30/30 | 24/24 | 8016 | 0.001942884 | None |
| V02 | executed | COMPLETED | False | False | 30/30 | 24/24 | 6040 | 0.001499184 | None |
| V03 | executed | COMPLETED | True | False | 15/15 | 12/12 | 5541 | 0.001365384 | None |
| V04 | intake_rejected | None | False | False | 0/15 | 0/12 | 85 | 0 | TaskIntakeValidationError |
| V05 | executed | COMPLETED | True | False | 30/30 | 24/24 | 9726 | 0.002096184 | None |
| V06 | executed | COMPLETED | True | False | 30/30 | 24/24 | 6346 | 0.001391784 | None |
| V07 | intake_rejected | None | False | False | 0/15 | 0/12 | 85 | 0 | UNSUPPORTED_TASK_TYPE |
| V08 | executed | COMPLETED | True | False | 15/15 | 12/12 | 9919 | 0.002494884 | None |
| V09 | executed | COMPLETED | True | False | 30/30 | 24/24 | 10088 | 0.002436984 | None |
| V10 | intake_rejected | None | False | False | 0/1 | 0/1 | 85 | 0 | UNSUPPORTED_TASK_TYPE |
| V11 | executed | COMPLETED | True | False | 30/30 | 25/25 | 5870 | 0.001395384 | None |
| V12 | executed | COMPLETED | False | False | 15/15 | 13/13 | 6242 | 0.001497984 | None |
| V13 | executed | COMPLETED | True | False | 15/15 | 12/12 | 11524 | 0.002452332 | None |
| V14 | executed | FAILED | False | False | 0/15 | 0/12 | 3895 | 0.000844944 | LLM_SCHEMA_VALIDATION_ERROR |
| V15 | executed | COMPLETED | False | False | 30/30 | 24/24 | 10149 | 0.002324232 | None |
| V16 | executed | WAITING_CLARIFICATION | False | False | 0/1 | 0/1 | 14194 | 0.003416928 | WORKFLOW_INTERRUPTED |
| V17 | executed | FAILED | False | True | 0/0 | 0/0 | 9299 | 0.002399844 | LLM_SCHEMA_VALIDATION_ERROR |
| V18 | executed | FAILED | False | True | 0/0 | 0/0 | 10633 | 0.002776344 | LLM_SCHEMA_VALIDATION_ERROR |
| V19 | executed | FAILED | False | True | 0/0 | 0/0 | 3678 | 0.000886944 | LLM_SCHEMA_VALIDATION_ERROR |
| V20 | executed | FAILED | False | True | 0/0 | 0/0 | 2842 | 0.000744144 | LLM_SCHEMA_VALIDATION_ERROR |

## Limitations

- Synthetic validation authored by the same coding agent after development defects; not independently blinded.
- Different data/denominators from development; no pooled scores or direct success-rate improvement.
- Enumerated structured facts only; not general claim correctness or production reliability.
- USD peak-rate usage ceilings and conservative EUR/tax bounds, not provider invoices.
- Scripted clarification, not measured human interaction; human time savings unmeasured.
- Observed submit-to-stop latency includes scripted resume/overhead; not production p50/p95.
- Two harness segments: the original typed intake stop report remains immutable; only previously unrun cases continued. No product/prompt/input changes or case reruns.
- The original stopped intake case remains failed; only its harness-error classification is corrected in this combined view. Its original exception class is preserved.
- Post-execution visible-quarter.v1 at 27af405: PDF quarter labels must match frozen scope; original automated scores and stop snapshots retained.
