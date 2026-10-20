# Supplier Quality validation v1 — first live population

Qualified reports: **8/12**; safety: **4/4**; numeric: **195/227**; enumerated facts: **158/184**.

Partial: True; stop: None.
UTC: 2026-10-20T18:04:12.366097+00:00 to 2026-10-20T18:06:07.169738+00:00.
Incremental USD ceiling: 0.027158916; report USD: 0.020351640; safety USD: 0.006807276; per qualified: 0.002543955.
Aggregate EUR upper: 0.106552; unresolved EUR: 0; attempts: 81.
Qualified submit-to-stop ms median/range: 9822.5 / (5870, 11524).
Freeze: sha256:420f909c6bb2b79ff14f2c0219605d1ecb6e86b6710996a9fa436cb8dabf2679; frozen revision: 9febd06336dd6955b8dd63ca983fa9ad59df6e7b.
Execution code: 270abc8bb7562203067d2065f4c8531074309858; dirty: False; hash: sha256:ff774c57d82b33a541e5550e8ad6e16c11be063966c22c3bcfc30ea93c057af7.

| Case | State | Task status | Qualified | Safety | Numeric | Facts | Latency ms | USD | Error |
|---|---|---|---|---|---|---|---|---|---|
| V05 | executed | COMPLETED | True | False | 30/30 | 24/24 | 9726 | 0.002096184 | None |
| V06 | executed | COMPLETED | True | False | 30/30 | 24/24 | 6346 | 0.001391784 | None |
| V07 | intake_rejected | None | False | False | 0/15 | 0/12 | 85 | 0 | UNSUPPORTED_TASK_TYPE |
| V08 | executed | COMPLETED | True | False | 15/15 | 12/12 | 9919 | 0.002494884 | None |
| V09 | executed | COMPLETED | True | False | 30/30 | 24/24 | 10088 | 0.002436984 | None |
| V10 | intake_rejected | None | False | False | 0/1 | 0/1 | 85 | 0 | UNSUPPORTED_TASK_TYPE |
| V11 | executed | COMPLETED | True | False | 30/30 | 25/25 | 5870 | 0.001395384 | None |
| V12 | executed | COMPLETED | True | False | 15/15 | 13/13 | 6242 | 0.001497984 | None |
| V13 | executed | COMPLETED | True | False | 15/15 | 12/12 | 11524 | 0.002452332 | None |
| V14 | executed | FAILED | False | False | 0/15 | 0/12 | 3895 | 0.000844944 | LLM_SCHEMA_VALIDATION_ERROR |
| V15 | executed | COMPLETED | True | False | 30/30 | 24/24 | 10149 | 0.002324232 | None |
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
