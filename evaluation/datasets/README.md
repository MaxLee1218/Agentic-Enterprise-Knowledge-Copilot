# Evaluation dataset authoring

`supplier_quality_v1.jsonl` is a versioned, sanitized offline dataset. Each non-blank line is one
strict `EvaluationCase`, and every case can run independently.

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

Oracle values are evaluator-only. Never copy `expected_*`, fixture answers, forbidden lists, or
numeric assertions into `TaskRequest`, LLM messages, ToolCall arguments, or Graph state. The loader
test for `agent_task_payload()` enforces the public subset.

## Validate and run

```bash
python evaluation/run_eval.py --case normal-q2-analysis
python evaluation/run_eval.py --tag security
pytest tests/unit/evaluation tests/contract/evaluation
```

Invalid JSON, missing/duplicate case IDs, illegal categories or statuses, negative tolerances,
unsafe/missing fixtures, or filters with no matches return exit code 2.
