# Stage 9 — Permission, Task API and console integration

**Status:** `COMPLETE — 2026-08-24`  
**Baseline:** Accounts Payable Invoice Compliance & Exception Investigation v1, design `1.0`  
**Production readiness:** `NOT CLAIMED`

## Delivered boundary

Stage 9 exposes `accounts_payable_analysis.v1` through the existing `/v1/tasks` collection,
detail, evidence, artifact metadata and artifact download resources. It does not introduce a
parallel `/v1/finance/*` API and does not bypass Task Service, Policy, Approval, shared Graph,
Evidence, Audit, Artifact or Verification boundaries.

The public task submission contract now accepts an optional `task_type` enum with the two frozen
values `supplier_quality_analysis.v1` and `accounts_payable_analysis.v1`. Omitting it preserves the
signed caller purpose behavior for existing clients. Clients using exhaustive enum switches must
adopt the added AP value.

## Authorization and trusted scope

- `finance_analyst` can submit, execute, inspect, download, generate reports and cancel its own AP
  tasks within signed AP scope.
- `finance_approver` provides the governed approval permission for AP actions that require it.
- `finance_auditor` has read-only task, Evidence and Artifact metadata access. Artifact download
  additionally requires the signed `finance:ap.artifact:download` scope.
- list, detail, Evidence and Artifact access are limited to owned or explicitly assigned task IDs
  and to signed allowed task types. Assignment does not grant cancellation.
- the requested task type must be present in the signed `allowed_task_types`; a browser-selected
  enum cannot expand authority.
- tenant, roles, scopes, assigned tasks, legal entities, business units, currencies, materiality,
  policy identity/snapshot and approval state are parsed only from the short-lived signed caller
  assertion. They are not accepted from the public request body.

The role evaluator selects only roles valid for the persisted task purpose, so a multi-domain
identity does not acquire cross-domain permissions by role union. Artifact authorization derives
purpose from the stored task before evaluating metadata or download access.

## Console and contract integration

The console adds a use-case selector on task creation, a stable use-case badge on list and detail
views, domain-neutral task copy, and an AP safe summary based only on authorized Artifact metadata.
The summary does not expose invoice findings or amounts outside the downloaded governed report.
No control is provided for tenant, role, scope, legal entity, business unit, currency, tool,
operation, query template, policy identity or approval state.

The checked-in OpenAPI snapshot and generated TypeScript schema include the expanded `TaskType`
enum and optional task submission field.

## Acceptance evidence

Automated coverage added for:

- AP submission and a complete 14-step AP execution through the existing Task API;
- Task list/detail, all three Evidence categories, Artifact metadata and download;
- task-type selection denial outside signed `allowed_task_types`;
- finance auditor assignment, read-only behavior and download-scope denial;
- signed AP identity parsing and tamper rejection;
- exact public enum and OpenAPI contract expansion;
- task selector request minimization, task-type badges and AP metadata-only summary;
- browser-flow request payload and authority-field exclusion.

Stage 9 has no database migration. The Supplier Quality API and console path remains available
through the same resources.

## Deferred gates

Stage 10 AP evaluation and security release gates, Stage 11 full local enterprise E2E and Stage 12
release/operations work are not part of this acceptance record. No live production-data,
performance, deployment or production-readiness claim is made.
