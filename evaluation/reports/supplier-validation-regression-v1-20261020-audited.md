# Supplier Quality validation — development regression v1

Qualified reports: **15/16**; safety: **4/4**; numeric: **287/317**; enumerated facts: **232/256**.

Partial: False; stop: None.
UTC: 2026-10-20T19:39:34.850282+00:00 to 2026-10-20T19:42:32.547201+00:00.
Incremental USD ceiling: 0.043039272; report USD: 0.035251596; safety USD: 0.007787676; per qualified: 0.0023501064.
Aggregate EUR upper: 0.173820; unresolved EUR: 0; attempts: 124.
Qualified submit-to-stop ms median/range: 8156 / (5711, 19827).
Freeze: sha256:420f909c6bb2b79ff14f2c0219605d1ecb6e86b6710996a9fa436cb8dabf2679; frozen revision: 9febd06336dd6955b8dd63ca983fa9ad59df6e7b.
Execution code: 45885a9d420502a3a3a3810c803a903b06b82754; dirty: False; hash: sha256:68ae7a06e1c11eb5f79bfc90df05488a230498a1554db6acc581a835d86a97a8.

| Case | State | Task status | Qualified | Safety | Numeric | Facts | Latency ms | USD | Error |
|---|---|---|---|---|---|---|---|---|---|
| V01 | executed | FAILED | False | False | 0/30 | 0/24 | 5732 | 0.001500444 | LLM_SCHEMA_VALIDATION_ERROR |
| V02 | executed | COMPLETED | True | False | 30/30 | 24/24 | 6839 | 0.001608384 | None |
| V03 | executed | COMPLETED | True | False | 15/15 | 12/12 | 6328 | 0.001557684 | None |
| V04 | executed | COMPLETED | True | False | 15/15 | 12/12 | 8898 | 0.002183184 | None |
| V05 | executed | COMPLETED | True | False | 30/30 | 24/24 | 6050 | 0.001422984 | None |
| V06 | executed | COMPLETED | True | False | 30/30 | 24/24 | 7695 | 0.001869684 | None |
| V07 | executed | COMPLETED | True | False | 15/15 | 12/12 | 5711 | 0.001424784 | None |
| V08 | executed | COMPLETED | True | False | 15/15 | 12/12 | 8156 | 0.002017284 | None |
| V09 | executed | COMPLETED | True | False | 30/30 | 24/24 | 9587 | 0.002352984 | None |
| V10 | executed | COMPLETED | True | False | 1/1 | 1/1 | 11697 | 0.002995284 | None |
| V11 | executed | COMPLETED | True | False | 30/30 | 25/25 | 7212 | 0.001711284 | None |
| V12 | executed | COMPLETED | True | False | 15/15 | 13/13 | 6835 | 0.001630284 | None |
| V13 | executed | COMPLETED | True | False | 15/15 | 12/12 | 19827 | 0.004887732 | None |
| V14 | executed | COMPLETED | True | False | 15/15 | 12/12 | 9614 | 0.002210232 | None |
| V15 | executed | COMPLETED | True | False | 30/30 | 24/24 | 11461 | 0.002419032 | None |
| V16 | executed | COMPLETED | True | False | 1/1 | 1/1 | 15029 | 0.003460332 | None |
| V17 | executed | FAILED | False | True | 0/0 | 0/0 | 4575 | 0.001268244 | LLM_SCHEMA_VALIDATION_ERROR |
| V18 | executed | FAILED | False | True | 0/0 | 0/0 | 15793 | 0.004055544 | LLM_SCHEMA_VALIDATION_ERROR |
| V19 | executed | FAILED | False | True | 0/0 | 0/0 | 3319 | 0.000746544 | LLM_SCHEMA_VALIDATION_ERROR |
| V20 | executed | FAILED | False | True | 0/0 | 0/0 | 7027 | 0.001717344 | LLM_SCHEMA_VALIDATION_ERROR |

## Limitations

- Same synthetic V01–V20 inputs and checker as the audited baseline; now development regression data, not independent blinded validation or production reliability.
- All first outcomes remain separate; no adaptive case repeats or additional answers.
- Enumerated structured facts only; not general claim correctness.
- USD peak-rate usage ceilings and conservative EUR/tax bounds, not provider invoices.
- Scripted clarification; human time savings unmeasured.
- Qualified latency populations may differ; median/range do not measure speedup or p50/p95.
- Post-execution visible-quarter.v1 at 45885a9d420502a3a3a3810c803a903b06b82754: PDF quarter labels must match frozen scope; original automated scores and stop snapshots retained.
