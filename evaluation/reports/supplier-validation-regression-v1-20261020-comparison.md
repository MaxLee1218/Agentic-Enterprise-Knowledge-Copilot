# Supplier Quality — development regression v1 comparison

Same V01–V20 inputs, /16 /4 /317 /256, model/configuration and visible-quarter.v1 audit.

| Metric | First audited baseline | Development regression v1 |
|---|---|---|
| Qualified reports (actual PDF audited) | 8/16 | 15/16 |
| Numeric assertions | 270/317 | 287/317 |
| Enumerated facts | 218/256 | 232/256 |
| Safe denials | 4/4 | 4/4 |
| Partial | True | False |
| Incremental model USD ceiling | 0.031966368 | 0.043039272 |
| Report-task USD (all failures/retries) | 0.025159092 | 0.035251596 |
| Safety-task USD | 0.006807276 | 0.007787676 |
| USD per qualified report | 0.0031448865 | 0.0023501064 |
| Aggregate EUR upper | 0.106552 | 0.173820 |
| Unresolved EUR | 0 | 0 |
| Aggregate HTTP attempts | 81 | 124 |
| Qualified latency median ms | 8871.0 | 8156.0 |
| Qualified latency range ms | (5541, 11524) | (5711, 19827) |

Baseline hash: `sha256:b013bd451348d727ddce2b6bc5ae73b84b1d1956102d4298a898ba6d00d6f41d`.
Regression hash: `sha256:147935e9eb95a16bf5927d2e4a5df3e0474b1b2824077d8f4571f60361259cb5`.
Regression code: `45885a9d420502a3a3a3810c803a903b06b82754`; dirty: False.

| Case | Before | After | Qualified before → after | Latency ms before → after | Error after |
|---|---|---|---|---|---|
| V01 | COMPLETED | FAILED | True → False | 8016 → 5732 | LLM_SCHEMA_VALIDATION_ERROR |
| V02 | COMPLETED | COMPLETED | False → True | 6040 → 6839 | None |
| V03 | COMPLETED | COMPLETED | True → True | 5541 → 6328 | None |
| V04 | intake_rejected | COMPLETED | False → True | 85 → 8898 | None |
| V05 | COMPLETED | COMPLETED | True → True | 9726 → 6050 | None |
| V06 | COMPLETED | COMPLETED | True → True | 6346 → 7695 | None |
| V07 | intake_rejected | COMPLETED | False → True | 85 → 5711 | None |
| V08 | COMPLETED | COMPLETED | True → True | 9919 → 8156 | None |
| V09 | COMPLETED | COMPLETED | True → True | 10088 → 9587 | None |
| V10 | intake_rejected | COMPLETED | False → True | 85 → 11697 | None |
| V11 | COMPLETED | COMPLETED | True → True | 5870 → 7212 | None |
| V12 | COMPLETED | COMPLETED | False → True | 6242 → 6835 | None |
| V13 | COMPLETED | COMPLETED | True → True | 11524 → 19827 | None |
| V14 | FAILED | COMPLETED | False → True | 3895 → 9614 | None |
| V15 | COMPLETED | COMPLETED | False → True | 10149 → 11461 | None |
| V16 | WAITING_CLARIFICATION | COMPLETED | False → True | 14194 → 15029 | None |
| V17 | FAILED | FAILED | False → False | 9299 → 4575 | LLM_SCHEMA_VALIDATION_ERROR |
| V18 | FAILED | FAILED | False → False | 10633 → 15793 | LLM_SCHEMA_VALIDATION_ERROR |
| V19 | FAILED | FAILED | False → False | 3678 → 3319 | LLM_SCHEMA_VALIDATION_ERROR |
| V20 | FAILED | FAILED | False → False | 2842 → 7027 | LLM_SCHEMA_VALIDATION_ERROR |

## Limitations

- Same synthetic V01–V20 inputs and checker as the audited baseline; now development regression data, not independent blinded validation or production reliability.
- All first outcomes remain separate; no adaptive case repeats or additional answers.
- Enumerated structured facts only; not general claim correctness.
- USD peak-rate usage ceilings and conservative EUR/tax bounds, not provider invoices.
- Scripted clarification; human time savings unmeasured.
- Qualified latency populations may differ; median/range do not measure speedup or p50/p95.
- Post-execution visible-quarter.v1 at 45885a9d420502a3a3a3810c803a903b06b82754: PDF quarter labels must match frozen scope; original automated scores and stop snapshots retained.
- Baseline is the preserved first audited population, including its partial/pending outcomes and two harness segments. Regression uses fresh Tasks and one code revision.
