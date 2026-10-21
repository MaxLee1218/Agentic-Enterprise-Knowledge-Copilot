# Supplier Quality P01 live calibration — 2026-10-19

First live run of P01, on synthetic supplier inspection aggregates. This is one calibration case,
not a ten-case benchmark, a production success rate, or a measured human-time saving.

| Observation | Result |
|---|---|
| Task | `T-0001`, `COMPLETED` |
| Provider / configured model | DeepSeek / `deepseek-flash` |
| Qualified delivery | 1/1 P01; product verification and independent acceptance passed |
| Numeric assertions | 30/30, including the zero denominator and null-reason checks |
| All structured acceptance assertions | 67/67 |
| End-to-end observed latency | 4,784 ms |
| Actual HTTP model attempts | 2, both reconciled |
| Provider input / output tokens | 2,197 / 695 |
| Reported reasoning tokens | 385, already included in the 695 output tokens |
| Usage-based USD fee at pinned rates | $0.0014931; not a provider invoice |
| Conservative EUR budget accounting | €0.002334, including FX and tax/fee contingencies |
| Unresolved reservations | €0 |

The model interpreted and planned the request. Production policy, Registry/Executor, the real
read-only SQLite adapter, deterministic analytics, report renderer and product verifier executed
the business workflow. The independent evaluator checked the delivered artifact against source
SQL and Decimal calculations. Expected answers were not model inputs.

Code revision: `096ca89bdbd5a5a19be5319eb8d9f5d44dc1f9b2`, clean at execution.
Source version: `supplier-quality-pilot-source.v1`.
Source hash: `sha256:8f5000a3d9d7d52cb1adf7701772c6fe3f42605bb9e5ebad435b53fe89496cdf`.
Code hash (including the ledger migration): `sha256:91664093063d626a27cb510a074165dda90c9904eb4fa7f476f27fbec63c225d`.
Configuration hash: `sha256:d399e7130d79bde6838237f6ae0599b2579050f3a1f6980f3b3f99cdc4c98ae7`.
Run ID: `bb7a21cde97c4cd788c66e8c92d7d57c`. Started UTC: `2026-10-19T08:15:20.304365Z`.

The [machine-readable result](../evaluation/reports/pilot-p01-live-20261019.json) contains all
assertions, prompt versions, the dated pricing/FX configuration and the two raw usage records.
The [delivered JSON artifact](../evaluation/reports/pilot-p01-live-20261019-artifact.json)
is copied byte-for-byte; only the result's artifact path is normalized for portability.
Both contain synthetic data. The full experiment ledger stays local and must be retained for
subsequent cases; the published snapshot is evidence, not a replacement/reset ledger.

Reproduce using the [live command and controls](evaluation.md#budgeted-live-p01-calibration).
Model outputs and latency can vary. The checked-in price card is date-limited; renewing it or
changing ledger pricing requires a reviewed migration that retains prior spending. No other
live case or rerun was performed for this result.

Validation: 941 passed, 35 skipped in the complete Python suite; Ruff, formatting, Mypy,
documentation/architecture checks, distribution build and both existing mock evaluation gates
passed (Supplier Quality 30/30; Accounts Payable 25/25). Standards review's ledger migration
finding was fixed and rechecked; Spec review found no remaining issues.

Interview wording supported now: “Completed one real-model Supplier Quality calibration on
synthetic data, delivered an evidence-backed report in 4.784 seconds, and passed 30/30 independent
numeric checks. Two model calls used 2,892 tokens; usage-based cost was $0.0014931 at the pinned
rates.” Broader delivery rates and time-saving claims await the remaining cases and a human study.
