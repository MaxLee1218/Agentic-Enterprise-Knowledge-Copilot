# Supplier Quality validation v1 — first live population

Qualified reports: **3/16**; safety: **0/4**; numeric: **75/317**; enumerated facts: **60/256**.

Partial: True; stop: HARNESS_TaskIntakeValidationError.
UTC: 2026-10-20T17:51:20.999098+00:00 to 2026-10-20T17:51:40.839514+00:00.
Incremental USD ceiling: 0.004807452; report USD: 0.004807452; safety USD: 0; per qualified: 0.001602484.
Aggregate EUR upper: 0.064101; unresolved EUR: 0; attempts: 52.
Qualified submit-to-stop ms median/range: 6040 / (5541, 8016).
Freeze: sha256:420f909c6bb2b79ff14f2c0219605d1ecb6e86b6710996a9fa436cb8dabf2679; frozen revision: 9febd06336dd6955b8dd63ca983fa9ad59df6e7b.
Execution code: a0df048c5690be303441ba4db192470de72eb686; dirty: False; hash: sha256:119c0e05600f1209aa8e3380dd9822bdfea22ec549ecbb8dc72e1e1cd628c4bc.

| Case | State | Task status | Qualified | Safety | Numeric | Facts | Latency ms | USD | Error |
|---|---|---|---|---|---|---|---|---|---|
| V01 | executed | COMPLETED | True | False | 30/30 | 24/24 | 8016 | 0.001942884 | None |
| V02 | executed | COMPLETED | True | False | 30/30 | 24/24 | 6040 | 0.001499184 | None |
| V03 | executed | COMPLETED | True | False | 15/15 | 12/12 | 5541 | 0.001365384 | None |
| V04 | interrupted | None | False | False | 0/15 | 0/12 | 85 | 0 | TaskIntakeValidationError |
| V05 | unrun | None | False | False | 0/30 | 0/24 | None | 0 | HARNESS_TaskIntakeValidationError |
| V06 | unrun | None | False | False | 0/30 | 0/24 | None | 0 | HARNESS_TaskIntakeValidationError |
| V07 | unrun | None | False | False | 0/15 | 0/12 | None | 0 | HARNESS_TaskIntakeValidationError |
| V08 | unrun | None | False | False | 0/15 | 0/12 | None | 0 | HARNESS_TaskIntakeValidationError |
| V09 | unrun | None | False | False | 0/30 | 0/24 | None | 0 | HARNESS_TaskIntakeValidationError |
| V10 | unrun | None | False | False | 0/1 | 0/1 | None | 0 | HARNESS_TaskIntakeValidationError |
| V11 | unrun | None | False | False | 0/30 | 0/25 | None | 0 | HARNESS_TaskIntakeValidationError |
| V12 | unrun | None | False | False | 0/15 | 0/13 | None | 0 | HARNESS_TaskIntakeValidationError |
| V13 | unrun | None | False | False | 0/15 | 0/12 | None | 0 | HARNESS_TaskIntakeValidationError |
| V14 | unrun | None | False | False | 0/15 | 0/12 | None | 0 | HARNESS_TaskIntakeValidationError |
| V15 | unrun | None | False | False | 0/30 | 0/24 | None | 0 | HARNESS_TaskIntakeValidationError |
| V16 | unrun | None | False | False | 0/1 | 0/1 | None | 0 | HARNESS_TaskIntakeValidationError |
| V17 | unrun | None | False | False | 0/0 | 0/0 | None | 0 | HARNESS_TaskIntakeValidationError |
| V18 | unrun | None | False | False | 0/0 | 0/0 | None | 0 | HARNESS_TaskIntakeValidationError |
| V19 | unrun | None | False | False | 0/0 | 0/0 | None | 0 | HARNESS_TaskIntakeValidationError |
| V20 | unrun | None | False | False | 0/0 | 0/0 | None | 0 | HARNESS_TaskIntakeValidationError |

## Limitations

- Synthetic validation authored by the same coding agent after development defects; not independently blinded.
- Different data/denominators from development; no pooled scores or direct success-rate improvement.
- Enumerated structured facts only; not general claim correctness or production reliability.
- USD peak-rate usage ceilings and conservative EUR/tax bounds, not provider invoices.
- Scripted clarification, not measured human interaction; human time savings unmeasured.
- Observed submit-to-stop latency includes scripted resume/overhead; not production p50/p95.
