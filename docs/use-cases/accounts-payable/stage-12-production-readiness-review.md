# Stage 12 — Regression and Production-Readiness Review

**Status:** `COMPLETE — 2026-08-24`  
**Frozen baseline:** Accounts Payable Invoice Compliance & Exception Investigation v1, design `1.0`  
**Review decision:** `NOT READY`  
**Release authorization:** `DENIED UNTIL BLOCKERS ARE CLOSED`

## Decision

Stage 12 is complete because the final evidence review has been performed, repository-controlled
gates have been strengthened, and every unresolved release condition has an explicit owner and
closure test. Completion of this review is not production approval. The frozen acceptance rule
allows a documented `NOT READY` result when blockers remain; silently supplying organization
policy, production credentials, owner approval, retention periods or live-system evidence would
contradict the baseline.

Use Case 2 remains a complete governed synthetic/local vertical slice. It must not be connected to
production finance data or presented as production ready until all blocking rows below pass.

## Repository-controlled changes accepted in Stage 12

1. The independent Accounts Payable dataset and baseline are now a mandatory CI regression gate
   alongside Supplier Quality and MCP evaluation.
2. The 50,000-row analytics performance gate still enforces the frozen 20-second absolute limit,
   while the wall-clock measurement is informational for baseline comparison. This removes a
   zero-tolerance sub-millisecond false regression without loosening the absolute gate.
3. Production configuration now requires `AP_POLICY_REQUIRE_PUBLISHED_SNAPSHOT=true` and rejects
   the repository's embedded demo policy paths. Production Compose requires explicit approved
   bundle and snapshot paths, mounts both read-only, and never publishes or repairs policy during
   API startup.
4. PostgreSQL CI now dry-runs custom-format backup and isolated restore for both Copilot persistence
   and the separate enterprise business database after their migration/integration gates.
5. Deployment and operations guidance now covers AP policy activation, both database trust
   boundaries, Artifact/policy/RAG recovery, retention/legal hold, and the AP evaluation command.

No AP formula, denominator, exception taxonomy, hard limit, Task/API state, permission, approval
edit rule, Evidence requirement or report contract changed.

## Final gate matrix

| Gate | Result | Evidence | Release consequence |
|---|---|---|---|
| Frozen contracts and business rules | PASS | Stages 1–9 and unchanged design/profile identifiers | No blocker |
| AP deterministic correctness/security | PASS | 25/25 synthetic cases; exact numeric/policy gates and zero unauthorized/leakage rates | No blocker |
| Supplier Quality compatibility | PASS | 30/30 unchanged baseline cases | No blocker |
| Shared Python/static/frontend gates | PASS | Ruff, format, strict Mypy, backend suite, OpenAPI, TypeScript, ESLint, Prettier, 31 frontend tests and production build | No blocker |
| Local Enterprise vertical slice | PASS | Stage 11 fresh-volume browser/RAG/PostgreSQL/Artifact evidence | Local/synthetic only |
| Production AP policy startup | PASS (code boundary) | Required read-only bundle/snapshot mounts and exact startup verification | Deployment must still supply approved tenant artifacts |
| SQLite/PostgreSQL migration and isolated rollback | PASS | Existing business and persistence migration suites | Production downgrade remains reviewed/destructive |
| Database backup/restore automation | PASS (CI contract) | CI dry-run added for both PostgreSQL trust boundaries | Actual environment rehearsal still blocking |
| Coordinated full restore | BLOCKED | No production rehearsal covering metadata, checkpoints, Artifact bytes, AP snapshots and RAG state at one recovery point | P1 release blocker |
| Retention and legal hold | BLOCKED | No owner-approved periods or tested cross-store deletion reconciliation; runtime retains records | P1 release blocker |
| Production identity and finance authorization | BLOCKED | Gateway adapter is implemented, but no target IdP/gateway mapping or finance-owner access review was supplied | P1 release blocker |
| Production policy/rule ownership | BLOCKED | Synthetic `TENANT-DEMO` bundle is not an approved deployment policy; no target-tenant owner release was supplied | P1 release blocker |
| External model/RAG data governance | BLOCKED | Accepted evaluations use offline/local model boundaries; no approved data-egress decision or live provider evaluation exists | P1 release blocker |
| Production data profiling and end-to-end load | BLOCKED | 50,000-row deterministic analytics passes, but no production-shaped database/RAG/report p95, concurrency, soak or capacity result exists | P1 release blocker |
| Security/architecture/business-owner sign-off | BLOCKED | Repository tests are green; named organizational approvers and signed threat/operations review are absent | P1 release blocker |
| High availability and disaster objectives | BLOCKED | No approved RPO/RTO, failover exercise or multi-instance operating evidence | P2 operational blocker |

## Verification evidence

The Stage 12 workspace verification uses base revision `41be9c2c395c517b0c137a467ccc4fbc5b2a700f`
with the Stage 11 and Stage 12 working changes present. The machine-readable command/result manifest
is stored at
[`evaluation/reports/accounts-payable-stage12/2026-08-24/manifest.json`](../../../evaluation/reports/accounts-payable-stage12/2026-08-24/manifest.json).

Key deterministic commands are:

```bash
pytest
ruff check .
ruff format --check .
mypy
python scripts/check_docs.py
python scripts/check_architecture.py
python evaluation/run_eval.py --mode mock --seed 42 \
  --baseline evaluation/baselines/supplier_quality_v1.json --fail-on-regression
python evaluation/run_eval.py --dataset evaluation/datasets/accounts_payable_v1.jsonl \
  --mode mock --seed 42 --baseline evaluation/baselines/accounts_payable_v1.json \
  --fail-on-regression
```

Frontend gates are `api:check`, `typecheck`, `lint`, `format:check`, `test`, `build`, and the
Playwright suite in CI. PostgreSQL CI runs the real persistence and business migration tests before
the two isolated restore rehearsals. Stage 11 remains the accepted local browser E2E rather than a
substitute for production infrastructure evidence.

## Required closure evidence

The decision can change to `READY` only after a new review records all of the following without
weakening a frozen test or baseline:

1. target-tenant policy documents/rules, checksums and snapshot published by named owners;
2. approved production identity/gateway mappings, finance roles/scopes and tenant isolation smoke;
3. retention, legal-hold and deletion reconciliation across every persisted store;
4. encrypted backup and isolated full restore with sampled Task/checkpoint/Artifact/policy/RAG
   integrity plus approved RPO/RTO;
5. production-shaped data-quality profiling and end-to-end query/analytics/report load, concurrency
   and soak results within all frozen limits;
6. approved model/RAG provider, data-egress decision and a versioned live evaluation;
7. clean-commit CI evidence including Supplier, AP, shared, frontend, PostgreSQL restore, Compose,
   security and evaluation gates; and
8. signed finance-data-owner, security, architecture and operations approval with no open P0/P1.

Until then, use the isolated synthetic environment for development and demonstration only.
