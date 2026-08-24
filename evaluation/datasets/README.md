# Evaluation dataset authoring

`supplier_quality_v1.jsonl` and `accounts_payable_v1.jsonl` are separate versioned, sanitized
offline datasets. Each non-blank line is one strict `EvaluationCase`, and every case can run
independently. The Supplier Quality dataset remains the default; AP must be selected explicitly
and has its own compatible baseline.

## Version and identifiers

- Increment the dataset version when a case, oracle, fixture, or metric meaning changes.
- Use lowercase kebab-case `case_id` values that remain stable across text-only edits.
- All rows in one file must share `dataset_id` and `dataset_version`.
- Case order does not affect execution; the loader sorts by `case_id`.

## Fixtures

Fixture references are relative to `fixtures/`, cannot escape that directory, and must exist at
load time. Fixtures contain synthetic Knowledge, database, analytics, LLM, or approval behavior.
They must not contain production records, secrets, network endpoints, or mutable shared state.
Fault injection is declared separately and uses deterministic call attempts.

## Expected/oracle semantics

Expected fields describe stable behavior: permitted status, semantic plan/tool constraints,
Evidence types and lineage, citation requirements, Decimal numeric assertions, safety boundaries,
and bounded recovery. They do not serialize an entire TaskState or require exact step prose/IDs.

Stage 15 security cases use explicit tags such as `prompt_injection`, `secret`,
`sensitive_data`, `unsafe_error`, and `artifact_authorization`. `required_audit_events` lists safe
event/finding markers that must be captured. Malicious payloads use fixed synthetic values only;
never add a real credential, personal record, connection string, or production path.

Oracle values are evaluator-only. Never copy `expected_*`, fixture answers, forbidden lists, or
numeric assertions into `TaskRequest`, LLM messages, ToolCall arguments, or Graph state. The loader
test for `agent_task_payload()` enforces the public subset.

The AP dataset is fixed at ID `accounts_payable`, version `1.0.0`, and seed 42. Its synthetic
fixtures declare their seed/profile metadata and cover normal, business-exception, boundary,
policy, authorization, security, planning and recovery inventory. AP precision and recall are
available only when at least one oracle supplies both positive and negative eligible labels; the
loader rejects an AP dataset that cannot satisfy that rule.

## Validate and run

```bash
python evaluation/run_eval.py --case normal-q2-analysis
python evaluation/run_eval.py --tag security
python evaluation/run_eval.py --dataset evaluation/datasets/accounts_payable_v1.jsonl
python evaluation/run_eval.py --dataset evaluation/datasets/accounts_payable_v1.jsonl \
  --baseline evaluation/baselines/accounts_payable_v1.json --fail-on-regression
pytest tests/unit/evaluation tests/contract/evaluation
```

Invalid JSON, missing/duplicate case IDs, illegal categories or statuses, negative tolerances,
unsafe/missing fixtures, or filters with no matches return exit code 2.
