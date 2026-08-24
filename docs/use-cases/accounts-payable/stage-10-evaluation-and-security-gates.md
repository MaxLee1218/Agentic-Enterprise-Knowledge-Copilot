# Stage 10 — AP evaluation and security gates

**Status:** `COMPLETE — 2026-08-24`  
**Frozen baseline:** Accounts Payable Invoice Compliance & Exception Investigation v1, design `1.0`  
**Dataset:** `accounts_payable` / `1.0.0` / seed `42`  
**Production readiness:** `NOT CLAIMED`

## Delivered boundary

Stage 10 adds an evaluation path independent of Supplier Quality while executing AP cases through
the production Task Service, shared Graph, Registry, Executor, Policy, Approval, Evidence,
Verifier and Artifact boundaries. The harness seeds a fresh synthetic SQLite business database
for every case and uses the real deterministic AP policy, database, analytics and reporting
adapters. Expected AP records, exclusions and summary values remain evaluator-owned and never
enter the Task request, model messages, Graph state or Tool arguments.

The dataset contains 25 independently runnable cases across normal, business-exception, boundary,
policy, authorization, security, planning and recovery categories. Loader validation requires the
complete frozen inventory, trusted `accounts_payable_analysis.v1` task type, safe fixture paths and
at least one positive-plus-negative oracle before a run can start. Supplier Quality remains the
default dataset and its baseline is stored separately.

## Deterministic AP metrics

The accepted versioned run is
[`20260824T030510.944113Z-54e646fd`](../../../evaluation/reports/accounts-payable-stage10/runs/20260824T030510.944113Z-54e646fd/report.md).
It records base Git revision `7518554d65995489476ada00db734f5773bc2c83`, dirty-tree status and
source hash `sha256:f835c0a9f1cff1b8f0d21b6fcb166dae468850326bc8862de2fc8c5ab471528e`.
The dataset hash is
`sha256:9e806a6c7531fd972576201cf5619850202aee20c9ba6f9b2877d6610908ad3d`; the
fixture hash is
`sha256:6c11921a3ab580a7b54464ad839b45ea56f3ea70f721ae5e9508624e09901f40`.

| Gate | Accepted result |
|---|---:|
| cases / task success | 25/25 / 100% |
| duplicate precision / recall | 1/1 / 1/1 |
| all-exception precision / recall | 7/7 / 7/7 |
| false-positive / false-negative rate | 0/16 / 0/7 |
| PO variance accuracy | 9/9 |
| payment-term accuracy | 12/12 |
| exception-amount accuracy | 7/7 |
| exclusion accuracy | 17/17 |
| policy-binding accuracy | 24/24 |
| Evidence coverage | 9/9 |
| citation correctness | 238/238 |
| report numeric replan recovery | 1/1 |

Precision and recall are computed only from exact evaluator labels with both positive and negative
eligible records. A missing eligible class produces `NOT_AVAILABLE` and fails the complete AP gate;
the evaluator never manufactures 100% from an empty denominator. The generic Supplier numeric
metric is not reused for AP; the exact AP Decimal, date, status, currency, exclusion and policy
metrics above are the frozen numeric authority.

## Security and authorization gates

The dataset covers unauthorized supplier/entity/unit/cross-tenant scope, detail without scope,
cross-principal Artifact access, restricted finance fields, prompt injection from the user,
document and database/tool-output source labels, malicious supplier text, raw SQL, write/payment
requests and approval bypass. Planning attack cases also attempt wrong tools, missing dependencies,
unsupported profiles and a Supplier template in an AP Plan.

Accepted zero-rate results are:

- unauthorized Tool execution: `0/4`;
- unauthorized table access: `0/1`;
- unauthorized field access: `0/4`;
- sensitive-data leakage: `0/1`;
- secret leakage: `0/1`;
- prompt-injection success: `0/1`;
- Artifact authorization failure: `0/1`.

Blocked attempts fail before unauthorized adapter execution, while safe interpretations may
complete only through allowlisted AP profiles and read-only templates. JSON and PDF report paths,
aggregate/detail isolation, round-trip parsing, Output Guard, checksum verification and Artifact
authorization are also covered by the complete shared test suite.

## Performance and recovery

The bounded performance fixture executes the production exact-duplicate operation three times over
exactly 50,000 synthetic invoice rows and 100 suppliers. The accepted p95 is `585.32 ms`, below the
frozen 20-second analytics ceiling; it emits `0` exception records, below the 5,000-record limit.
Peak Python memory observed by `tracemalloc` is `267,537,856` bytes and is informational because the
frozen baseline defines no process-memory hard limit.

Recovery cases cover transient database, analytics and report faults, retry exhaustion, report
numeric mismatch/replan, approval approve/edit/reject and checkpoint restart/resume. Regenerated
AP report work receives a planning-versioned step ID so immutable completed source steps are not
replayed. The shared runtime retains the original Supplier Quality step ceiling and grants only the
bounded extra AP report-replan capacity configured by retry/replan limits.

## Three-suite acceptance evidence

- AP evaluation: 25/25 passed; quality gate `PASS`; independent baseline written to
  `evaluation/baselines/accounts_payable_v1.json`.
- Supplier Quality evaluation: 30/30 passed against the unchanged
  `evaluation/baselines/supplier_quality_v1.json`; no regression.
- shared backend suite: 734 passed, 9 opt-in environment tests skipped, one dependency deprecation
  warning; the five PostgreSQL business tests were then enabled explicitly and all 5 passed.
- SQLite business upgrade/downgrade preserves Supplier Quality tables and rows.
- PostgreSQL AP migration/seed/downgrade/upgrade, five-template SQLite parity, runtime write denial
  and governed database workflow all pass against the isolated local PostgreSQL service.
- Ruff lint and formatting, strict mypy and documentation checks pass.

No Stage 10 schema migration is introduced. PostgreSQL was used only to verify the existing Stage 2
business migration history and was stopped after the gate.

## Artifacts and reproduction

- [AP evaluation baseline](../../../evaluation/baselines/accounts_payable_v1.json)
- [versioned human-readable report](../../../evaluation/reports/accounts-payable-stage10/runs/20260824T030510.944113Z-54e646fd/report.md)
- [versioned machine report](../../../evaluation/reports/accounts-payable-stage10/runs/20260824T030510.944113Z-54e646fd/report.json)
- [run manifest](../../../evaluation/reports/accounts-payable-stage10/runs/20260824T030510.944113Z-54e646fd/manifest.json)

```bash
python evaluation/run_eval.py \
  --dataset evaluation/datasets/accounts_payable_v1.jsonl \
  --mode mock \
  --seed 42 \
  --baseline evaluation/baselines/accounts_payable_v1.json \
  --fail-on-regression
```

## Deferred gates

This acceptance uses synthetic offline data and the deterministic offline provider
`offline-accounts-payable-eval-v1`. It does not establish production ERP data quality, live model
quality, external-system interoperability, deployed browser-to-RAG-to-PostgreSQL topology or
production operational readiness. Those remain Stage 11 and Stage 12 boundaries.
