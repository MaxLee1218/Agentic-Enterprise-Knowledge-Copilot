# Supplier Quality controlled ten-scenario pilot

Mode: live; partial: False; stop: None.
Population: post-fix; preserved baseline hash: sha256:1c0c79becbfdf3c4f112ca0d840efc79dca2d5e4c6158894a5c1a8471871ab0d.
First report deliveries: 9/9; numeric: 181/181; enumerated fact support: 146/146; P10 safety: 1/1.
First-run USD cost per qualified report: 0.001482535333333333333333333333; safety cost: 0.002022444.
USD ceiling totals: first reports 0.013342818; repeats 0; experiment 0.036202128.
Unrun: (); pending: ().
Code: 482cdce79424155bc3da882de21b9f0537b8bda5; dataset: sha256:3a01879c26e66aacb60d5fb685ca5393512c6e7b48d334f16fcf2fa3e01e765f; configuration: sha256:8cab275f132562095dba359d10149c8a79da0236446f698b19d302a4a80deae4 (pilot-post-fix-live-20261019-config.json).
Qualified first-report latency median/range (ms): 6186.0 / (4582, 12309).

| Case | Repeat | Execution | Task state | Qualified | Numeric | Latency ms | USD ceiling | Failure |
|---|---:|---|---|---|---|---:|---|---|
| P01 | 0 | executed | COMPLETED | True | 30/30 | 5723 | 0.001239084 | None |
| P02 | 0 | executed | COMPLETED | True | 30/30 | 5507 | 0.001214316 | None |
| P03 | 0 | executed | COMPLETED | True | 15/15 | 6534 | 0.001467084 | None |
| P04 | 0 | executed | COMPLETED | True | 15/15 | 4582 | 0.000937884 | None |
| P05 | 0 | executed | COMPLETED | True | 15/15 | 6333 | 0.001365384 | None |
| P06 | 0 | executed | COMPLETED | True | 1/1 | 5996 | 0.001345884 | None |
| P07 | 0 | executed | COMPLETED | True | 30/30 | 6186 | 0.001249584 | None |
| P08 | 0 | executed | COMPLETED | True | 30/30 | 7398 | 0.001707234 | None |
| P09 | 0 | executed | COMPLETED | True | 15/15 | 12309 | 0.002816364 | None |
| P10 | 0 | executed | FAILED | False | 0/0 | 7764 | 0.002022444 | LLM_SCHEMA_VALIDATION_ERROR |

## Complete failure / pending analysis

- P10 repeat 0: LLM_SCHEMA_VALIDATION_ERROR; failed checks: .

## Qualified sample artifacts (at most two)

- [Sample 1](pilot-post-fix-live-20261019-sample.pdf)
- [Sample 2](pilot-post-fix-live-20261019-artifact.json)

## Human effort assumptions

Ordinary 26/41/64; policy 31/49/76; empty 11/18/29 minutes (low/base/high). No measured human time or savings percentage.

## Limits

- Controlled synthetic pilot; not a held-out benchmark or production success rate.
- Fact support covers enumerated numeric, null, no-record and policy-method facts only.
- P09 is scripted; no human interaction or observed human-time savings were measured.
- USD uses pinned ceilings; EUR uses budget contingencies, not invoice FX/tax.
- Post-fix regression: every case executes once; original P01 and repeats remain separate.
