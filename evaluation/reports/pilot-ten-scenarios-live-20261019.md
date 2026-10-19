# Supplier Quality controlled ten-scenario pilot

Mode: live; partial: False; stop: None.
First report deliveries: 4/9; numeric: 175/181; enumerated fact support: 128/146; P10 safety: 1/1.
First-run USD cost per qualified report: 0.003927726; safety cost: 0.002154444.
USD ceiling totals: first reports 0.015710904; repeats 0.002971518; experiment 0.020836866.
Unrun: (); pending: ().
Code: 3c4bf61443c2fe51b9cea955d756e017cdab125f; dataset: sha256:3a01879c26e66aacb60d5fb685ca5393512c6e7b48d334f16fcf2fa3e01e765f; configuration: sha256:a38f6efe10710d146651d4a2d3f9007f184465f06c1808f71810d0303c972a7b ([pilot-ten-scenarios-live-20261019-config.json](pilot-ten-scenarios-live-20261019-config.json)).
Qualified first-report latency median/range (ms): 5371.5 / (4784, 6063).

| Case | Repeat | Execution | Task state | Qualified | Numeric | Latency ms | USD ceiling | Failure |
|---|---:|---|---|---|---|---:|---|---|
| P01 | 0 | executed | COMPLETED | True | 30/30 | 4784 | 0.0014931 | None |
| P02 | 0 | executed | COMPLETED | True | 30/30 | 5164 | 0.001082316 | None |
| P03 | 0 | executed | COMPLETED | True | 15/15 | 6063 | 0.001539516 | None |
| P04 | 0 | executed | COMPLETED | False | 15/15 | 9066 | 0.002357916 | None |
| P05 | 0 | executed | COMPLETED | True | 15/15 | 5579 | 0.001311816 | None |
| P06 | 0 | executed | COMPLETED | False | 1/1 | 6748 | 0.001540716 | None |
| P07 | 0 | executed | COMPLETED | False | 24/30 | 8548 | 0.002028684 | None |
| P08 | 0 | executed | COMPLETED | False | 30/30 | 6085 | 0.001575312 | None |
| P09 | 0 | executed | COMPLETED | False | 15/15 | 11606 | 0.002781528 | None |
| P10 | 0 | executed | FAILED | False | 0/0 | 8875 | 0.002154444 | LLM_SCHEMA_VALIDATION_ERROR |
| P01 | 1 | executed | COMPLETED | True | 30/30 | 4930 | 0.001156284 | None |
| P08 | 1 | executed | COMPLETED | False | 30/30 | 8156 | 0.001815234 | None |

## Complete failure / pending analysis

- P04 repeat 0: None; failed checks: scope_prose_matches_quarter.
- P06 repeat 0: None; failed checks: scope_prose_matches_quarter.
- P07 repeat 0: None; failed checks: S-100/2026-04/period_over_period_trend, S-100/2026-05/period_over_period_trend, S-100/2026-06/period_over_period_trend, S-200/2026-04/period_over_period_trend, S-200/2026-05/period_over_period_trend, S-200/2026-06/period_over_period_trend, exact_metric_set, null_reasons, support/S-100/2026-04/period_over_period_trend, support/S-100/2026-05/period_over_period_trend, support/S-100/2026-06/period_over_period_trend, support/S-200/2026-04/period_over_period_trend, support/S-200/2026-05/period_over_period_trend, support/S-200/2026-06/period_over_period_trend.
- P08 repeat 0: None; failed checks: policy_no_unsupported_actions.
- P09 repeat 0: None; failed checks: exact_metric_set, report_scope, database_matches_source, calculation_lineage, support/S-100/2026-04/defect_count, support/S-100/2026-04/inspected_count, support/S-100/2026-04/defect_rate, support/S-100/2026-04/period_over_period_trend, support/S-100/2026-05/defect_count, support/S-100/2026-05/inspected_count, support/S-100/2026-05/defect_rate, support/S-100/2026-05/period_over_period_trend, support/S-100/2026-06/defect_count, support/S-100/2026-06/inspected_count, support/S-100/2026-06/defect_rate, support/S-100/2026-06/period_over_period_trend.
- P10 repeat 0: LLM_SCHEMA_VALIDATION_ERROR; failed checks: .
- P08 repeat 1: None; failed checks: policy_no_unsupported_actions.

## Qualified sample artifacts (at most two)

- [Sample 1](pilot-ten-scenarios-live-20261019-sample.pdf)
- [Sample 2](pilot-p01-live-20261019-artifact.json)

## Human effort assumptions

Ordinary 26/41/64; policy 31/49/76; empty 11/18/29 minutes (low/base/high). No measured human time or savings percentage.

## Limits

- Controlled synthetic pilot; not a held-out benchmark or production success rate.
- Fact support covers enumerated numeric, null, no-record and policy-method facts only.
- P09 is scripted; no human interaction or observed human-time savings were measured.
- USD uses pinned ceilings; EUR uses budget contingencies, not invoice FX/tax.
- P01 retains its earlier code revision; it is re-scored and never replaced by a repeat.

## Publication and reproduction

Only Artifact path strings are normalized in the JSON snapshot. All assertions, hashes, usage records and fees are retained. Two qualified examples are linked above; other artifacts and capture records remain in the local retained experiment directory. The published snapshot never replaces or resets the spending ledger.

Frozen input/configuration: [pilot-ten-scenarios-live-20261019-config.json](pilot-ten-scenarios-live-20261019-config.json). Model and prompt versions are recorded per case in the JSON. Price card: `deepseek-flash-peak-2026-10-19.v1` (see the exact dated version and FX contingency in spending.pricing). Run with the documented `--suite --mode live` command and the dedicated migrated PostgreSQL runtime described in [evaluation.md](../../docs/evaluation.md#ten-scenario-controlled-pilot). The current price card is date limited; do not start a fresh paid population by copying/resetting the ledger.
