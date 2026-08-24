# Accounts Payable Invoice Compliance & Exception Investigation v1

**Design status:** `FROZEN — STAGE 12 REVIEW COMPLETE; PRODUCTION NOT READY`
**Task type:** `accounts_payable_analysis.v1`  
**Design version:** `1.0`  
**Date:** 2026-08-24

This directory is the frozen implementation authority for Use Case 2. It does not alter the
frozen Supplier Quality Analysis v1.1 baseline in `docs/design/`.
Stages 1 through 11 implement the AP contracts/routing, isolated synthetic data, controlled policy,
read-only query, deterministic analytics, independent verification and JSON/PDF reporting
foundations, governed execution through the existing Task Service and shared Graph, exposure
through the existing permission-scoped Task API and console, and an independent synthetic AP
evaluation/security baseline, and a fresh-volume browser-to-controlled-knowledge-to-real-
PostgreSQL-to-Artifact local E2E. Stage 11 uses synthetic data and does not establish production
readiness. Stage 12 completes the evidence-based release review and records a `NOT READY` decision
with explicit deployment, retention, recovery, performance and organizational blockers.

## Business outcome

The use case lets an authorized finance or procurement user investigate supplier invoices for a
bounded invoice-date range, compare operational facts with controlled AP and procurement policy,
detect deterministic exceptions, and generate an internal JSON or PDF management report with
claim-level lineage. It reduces manual reconciliation effort and improves duplicate-payment,
procurement-compliance, payment-timeliness, and audit review without asserting an unmeasured ROI.

## Frozen v1 capability

The v1 slice supports:

- exact duplicate invoice groups;
- invoice-to-PO amount variance for a single-invoice PO matching basis;
- missing required PO, including controlled approved exceptions;
- late and materially early payment for exactly one settled payment;
- overpayment for exactly one settled payment;
- deterministic exception summaries and supplier exception rates;
- policy comparison through version-bound Document Evidence and controlled rules;
- internal JSON and PDF reports.

The task is read-only. It may `ANALYZE`, `DETECT`, `COMPARE`, and `REPORT`; it may not mutate an
invoice, PO, payment, supplier, bank account, or external system.

## Document map

| Document | Authority |
|---|---|
| [Architecture](architecture.md) | Shared runtime, domain capability manifests, execution flow, limits, API/UI impact |
| [Domain model](domain-model.md) | Scope taxonomy, entities, roles, terminology, relationships |
| [Task contract](task-contract.md) | Trusted/untrusted boundary and versioned Pydantic contract proposal |
| [Tool contracts](tool-contracts.md) | Shared capability profiles, query templates, failures, idempotency |
| [Database design](database-design.md) | AP operational schema, tenant keys, precision, migration and seed plan |
| [Analytics design](analytics-design.md) | Deterministic algorithms, formulae, thresholds and outputs |
| [Evidence and verification](evidence-and-verification.md) | Evidence metadata, claim lineage, verifier extensions |
| [Security and governance](security-and-governance.md) | Roles, scopes, approvals, classification and threat model |
| [Evaluation plan](evaluation-plan.md) | Dataset, metrics, test layers and regression gates |
| [Platform reuse audit](platform-reuse-audit.md) | Code-backed current-state and coupling matrix |
| [Implementation plan](implementation-plan.md) | Staged delivery with acceptance criteria |
| [Design baseline](design-baseline.md) | Frozen identifiers, rules, limits, authority and change control |
| [Stage 0 design review](design-review.md) | Architecture, security, data-owner lens and gate results |
| [Stage 1 compatibility matrix](stage-1-compatibility-matrix.md) | Implemented domain contracts, profiles, upcasting and execution-denial boundary |
| [Stage 2 schema and seed report](stage-2-schema-and-seed.md) | Implemented business migrations, AP fact schema, deterministic fixture profile and verification |
| [Stage 3 policy corpus and rules report](stage-3-policy-corpus-and-rules.md) | Implemented controlled documents, exact rule bindings, immutable snapshot publication and verification |
| [Stage 4 AP database query templates report](stage-4-ap-database-query-templates.md) | Implemented five allowlisted AP read models, exact scope controls and Database Evidence |
| [Stage 5 deterministic AP analytics report](stage-5-deterministic-ap-analytics.md) | Implemented seven strict operations, Decimal/date calculations, lineage validation and Calculation Evidence batching |
| [Stage 6 AP Evidence and verifier profiles report](stage-6-ap-evidence-and-verifier-profiles.md) | Implemented structured claim mapping, AP metadata/policy/consistency/numeric rules and domain Safety allowlists |
| [Stage 7 AP report model and JSON/PDF report](stage-7-ap-report-model-and-renderers.md) | Implemented strong AP report model, deterministic JSON/PDF renderers, governed parser and atomic Artifact profile |
| [Stage 8 understanding, planner and shared Graph](stage-8-understanding-planner-and-shared-graph.md) | Implemented trusted AP understanding, exact 14-step Plan profile, policy/approval/input wiring and shared-Graph execution |
| [Stage 9 permission, Task API and console integration](stage-9-permission-api-and-frontend.md) | Implemented finance authorization profiles, trusted AP scope, existing Task resource integration, public enum expansion, console selector/badges/safe summary and regenerated contracts |
| [Stage 10 AP evaluation and security gates](stage-10-evaluation-and-security-gates.md) | Implemented independent AP dataset/oracles, deterministic metrics, attack and recovery cases, 50,000-row performance gate, baseline and versioned report |
| [Stage 11 full local enterprise E2E](stage-11-local-enterprise-e2e.md) | Accepted isolated fresh-volume dual-use-case topology, AP clean/mixed JSON/PDF, approval restart, real PostgreSQL, formal Supplier RAG and real Chrome download verification |
| [Stage 12 production-readiness review](stage-12-production-readiness-review.md) | Final regression/operations review, fail-closed production policy inputs, CI AP baseline and explicit NOT READY blockers |

## Terminology authority

The following names are canonical across this design:

- `gross_amount`: invoice amount used by duplicate, PO variance, materiality and overpayment rules.
- `approved_amount`: approved PO header amount for the v1 single-invoice matching basis.
- `payment_amount`: amount of the one eligible settled payment.
- `variance_amount = gross_amount - approved_amount`.
- `variance_rate = variance_amount / approved_amount`.
- `days_late = max(payment_date - due_date, 0 calendar days)`.
- `days_early = max(due_date - payment_date, 0 calendar days)`.
- `exception_invoice_count`: unique invoices having at least one v1 exception.
- `exception_invoice_amount_by_currency`: sum of `gross_amount` once per exception invoice.

Amounts are `Decimal` values; database storage uses `NUMERIC(20,4)`. No v1 metric aggregates
different currencies and no foreign-exchange conversion is performed.

## Status and approval boundary

The design reaches all nine architecture acceptance gates described in the
[Stage 0 design review](design-review.md). ADR-009, ADR-010 and ADR-011 record the accepted
architecture decisions, and [the baseline](design-baseline.md) freezes the implementation
authority. The accurate product status remains:

```text
ACCOUNTS PAYABLE USE CASE: DESIGN FROZEN
STAGE 0: COMPLETE
STAGE 1: COMPLETE — CONTRACTS AND MANIFEST ROUTING ONLY
STAGE 2: COMPLETE — ISOLATED BUSINESS SCHEMA AND DETERMINISTIC SEED ONLY
STAGE 3: COMPLETE — CONTROLLED POLICY CORPUS AND RULE MANIFEST ONLY
STAGE 4: COMPLETE — FIVE GOVERNED READ MODELS; WORKFLOW STILL DISABLED
STAGE 5: COMPLETE — SEVEN DETERMINISTIC OPERATIONS; WORKFLOW STILL DISABLED
STAGE 6: COMPLETE — AP EVIDENCE AND VERIFIER PROFILE; WORKFLOW STILL DISABLED
STAGE 7: COMPLETE — AP REPORT MODEL AND JSON/PDF PROFILE; WORKFLOW STILL DISABLED
STAGE 8: COMPLETE — INTERNAL TASK SERVICE AND SHARED GRAPH EXECUTION
STAGE 9: COMPLETE — PERMISSION-SCOPED PUBLIC TASK API AND CONSOLE INTEGRATION
STAGE 10: COMPLETE — INDEPENDENT SYNTHETIC EVALUATION AND SECURITY GATES
STAGE 11: COMPLETE — FULL LOCAL ENTERPRISE E2E FOR BOTH FROZEN USE CASES
UC2 TOOL EXECUTION: ENABLED THROUGH THE GOVERNED SHARED TASK WORKFLOW
STAGE 12: COMPLETE — EVIDENCE-BASED REVIEW; RELEASE BLOCKED
PRODUCTION READINESS: NOT READY
```

## Non-goals

v1 does not include automatic payment; invoice, PO, supplier-master, or bank-account mutation;
business approval of an invoice/payment; bank instructions; ERP/SAP integration; OCR/email
ingestion; multiple or partial payments; credit notes; three-way matching; invoice/PO line
matching; fuzzy duplicate matching; duplicate payment; unapproved vendor detection; split invoice
or threshold-avoidance detection; suspicious round or weekend payments; tax matching; bank-change
analysis; open SQL; arbitrary Python; a general finance chatbot; cross-domain supplier risk
scoring; multi-Agent execution; LoRA/fine-tuning; or an MCP dependency.
