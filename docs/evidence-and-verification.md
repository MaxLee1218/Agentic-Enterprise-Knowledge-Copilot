# Evidence Ledger and Deterministic Verification

## Scope and design authority

This document describes the implemented Stage 8 evidence and verification boundary for
`supplier_quality_analysis.v1`. The frozen files under `docs/design/` remain authoritative.
In particular, the frozen workflow generates the report Artifact before entering `VERIFYING`;
verification then gates `COMPLETED`. Stage 8 does not move the lifecycle gate ahead of report
generation.

The implementation does not use an LLM, parse Markdown, execute SQL, rerun analytics, or introduce
a report-claim Evidence type. Structured report claims are adapters over the current JSON report
output.

## Evidence ownership

`EvidenceItem` remains the frozen immutable domain contract and permits only `DOCUMENT`,
`DATABASE`, and `CALCULATION`. `TaskState` remains the small authoritative lifecycle snapshot and
does not contain mutable Evidence.

`InMemoryEvidenceLedger` is the current authoritative Evidence store. It:

- binds tool drafts to Task, Step, and ToolCall identifiers;
- appends immutable Evidence after `ToolExecutor` succeeds;
- indexes and deduplicates records within a Task;
- returns detached deep copies so callers cannot mutate nested JSON held by the Ledger;
- supports task-scoped lookup, step/type lookup, reference validation, snapshots, and restoration;
- traces direct and indirect calculation parents without crossing Task boundaries.

The runner keeps only a task-local read view for orchestration. It does not become a second
persistence owner.

## Stable deduplication

Every logical fingerprint is canonical JSON encoded with sorted keys and hashed with SHA-256.
Python's process-randomized `hash()` is never used. Timestamp, Evidence ID, and ToolCall ID are not
content identity.

All fingerprints include Task ID, Step ID, Evidence type, and a normalized content hash. Additional
type-specific fields are:

| Evidence type | Type-specific identity |
|---|---|
| Document | structured source reference, including document/version/location where present |
| Database | `query_fingerprint` (the frozen v1 query identifier), query metadata, and sorted table names |
| Calculation | trimmed formula/formula map, sorted input Evidence IDs, grouping/operation/version metadata |

Different Tasks never deduplicate into one record. A duplicate append returns
`EvidenceAddResult(created=false, duplicate_of=<canonical ID>)`; the Ledger does not append another
logical record.

## Lineage graph

Calculation Evidence uses the frozen
`EvidenceSourceReference.input_evidence_ids` field. There is no parallel lineage field.

`trace_lineage(task_id, evidence_id)` returns a `LineageTrace` with:

- the root Evidence;
- root-first, deterministic Evidence ordering;
- explicit parent-to-child edges;
- direct and indirect ancestors exactly once;
- completeness and structured issues.

Parents are traversed in lexical Evidence-ID order. Append and restore reject missing parents,
cross-task parents, self-reference, duplicate edges, and two-node or multi-node cycles. Snapshot
restoration validates the complete graph independently of serialized item order.

The required data derivation is:

```text
DATABASE Evidence
  -> CALCULATION Evidence (input_evidence_ids contains the database Evidence ID)
  -> structured DATA/NUMERIC Claim (citation contains the calculation Evidence ID)
```

Claims are not Evidence and do not extend the frozen `EvidenceType` enum.

## Required source metadata

- Document Evidence needs a stable document/source identifier and a location such as chunk or
  page.
- Database Evidence needs the frozen `query_fingerprint`; the implementation accepts `query_id`
  as a compatible external alias but does not change the v1 contract.
- Database audit metadata records authorized tables, authorized columns, `statement_type=SELECT`,
  and `read_only=true`.
- Calculation Evidence needs at least one input Evidence ID, formula/version metadata, and
  structured metric results.

Evidence content remains minimized and classified. Logs and issues contain identifiers, codes,
counts, and safe metadata rather than SQL, rows, tokens, document bodies, or secrets.

## Verification contracts

`VerificationStatus` is one of:

- `FAILED`: at least one `ERROR`;
- `PASSED_WITH_WARNINGS`: no Error and at least one `WARNING`;
- `PASSED`: no Error or Warning.

`VerificationIssue` records a stable code, safe message, severity, verifier, Task/Step/Claim
association, Evidence IDs, and bounded details. `VerificationResult` also records checks,
timestamp, duration, counts, trace ID, and verified Evidence IDs. The model validates that counts
and aggregate status agree with the issue list.

## Verifiers

### EvidenceStructureVerifier

Checks Task and Step ownership, required source metadata, StepResult references, calculation
parents, complete lineage, and the required database ancestry for calculations.

### DeliverableVerifier

Maps the frozen `ExpectedOutput.required_sections` to exact structured section identifiers.
It checks presence, non-empty structured content, the successful report-producing step, and
Evidence references. Extra sections are allowed but cannot replace a required section. A legitimate
business-empty result must be explicitly represented as empty; an empty object cannot masquerade
as a completed analysis.

### CitationVerifier

Checks structured `CitationClaim` objects, never citation-looking strings. It resolves each
Evidence ID in the current Task, validates source metadata and lineage, and enforces:

- policy claims trace to Document Evidence;
- data claims trace to Database Evidence;
- numeric claims cite Calculation Evidence that traces to Database Evidence.

### NumericVerifier

Reads baseline values only from structured Calculation Evidence. It does not recompute business
metrics or infer numbers from prose.

Counts are integral exact matches. Ratio and ratio-delta values use `Decimal`, the existing
`quality_metrics.v1` four-decimal precision, half-unit rounding tolerance, and `ROUND_HALF_EVEN`.
Units and percentage scale are never converted silently. Null values retain the existing
zero-denominator semantics. NaN and infinity are rejected.

### SafetyVerifier

Checks the Registry snapshot, validated plan, Contract capabilities, governed ToolCall/ToolResult
lineage, read-only capability set, approval binding when approval is required, database table and
column allowlists, SELECT/read-only audit metadata, and configured sensitive output fields.

It verifies structured database audit metadata rather than parsing or executing SQL. Confirmed
permission, approval, write, table, column, or sensitive-field violations are always Errors.
The current fixed workflow remains pre-authorized and read-only; Stage 8 does not implement the
future human-approval workflow.

### ArtifactIntegrityVerifier and CompositeVerifier

The workflow adapter checks the single Artifact's Task/type, governed location, size, SHA-256,
Evidence coverage, and JSON structure. It then maps the report to `CandidateResult` and runs every
safe verifier, including SafetyVerifier even when earlier checks fail.

## Workflow integration

The implemented frozen sequence is:

```text
Knowledge Tool
  -> Database Tool
  -> Analytics Tool
  -> Report Tool / PDF or JSON Artifact
  -> TaskStatus.VERIFYING
  -> deterministic verification
  -> COMPLETED or FAILED
```

The runner persists `VerificationResult` and emits a safe `verification_completed` audit event.
`PASSED` and `PASSED_WITH_WARNINGS` may transition to `COMPLETED`; warnings remain available in the
persisted result. `FAILED` triggers `NON_REPAIRABLE_VERIFICATION_FAILURE`, prevents `COMPLETED`,
omits the unverified Artifact from `TaskResult.artifacts`, and preserves the Artifact, Evidence,
ToolResults, and audit trail for diagnosis.

The attachment's proposed pre-report lifecycle order conflicts with the frozen state machine and
walkthrough. Input schemas and policy still guard report execution, but the authoritative
verification gate remains after Artifact generation.

## Supplier Quality trace example

```text
Task T-001
  S-DB / TC-DB
    E-DB
      query_fingerprint=sha256:...
      tables=incoming_inspections,suppliers
      row_count=6
  S-AN / TC-AN
    E-CALC
      input_evidence_ids=[E-DB]
      formula=defect_count / inspected_count
      metric defect_rate=0.0150 ratio
  S-RP / TC-RP
    JSON claim report:metric:defect_rate
      value=0.0150 ratio
      citations=[E-CALC]
  VERIFYING
    E-CALC -> E-DB lineage complete
    numeric claim equals Calculation Evidence
    query tables/columns authorized
    Artifact checksum and citations valid
  COMPLETED
```

## Common failures

| Code | Meaning |
|---|---|
| `EVIDENCE_NOT_FOUND` | scoped Evidence ID does not exist |
| `LINEAGE_CROSS_TASK_REFERENCE` | parent belongs to another Task |
| `LINEAGE_CYCLE` | restored lineage contains a cycle |
| `DATABASE_QUERY_ID_MISSING` | database source lacks a query fingerprint |
| `DELIVERABLE_MISSING` | required structured section is absent |
| `CITATION_REFERENCE_INVALID` | claim cites missing/cross-task Evidence |
| `CITATION_TYPE_INCOMPATIBLE` | claim and Evidence source type do not match |
| `NUMERIC_CLAIM_MISMATCH` | report number differs from Calculation Evidence |
| `APPROVAL_SCOPE_INVALID` | approval status, plan, action, scope, or validity does not match |
| `DATABASE_TABLE_NOT_ALLOWED` | database audit metadata contains an unauthorized table |
| `SENSITIVE_FIELD_OUTPUT` | candidate exposes a configured sensitive field |

## Stage 8 and Stage 9 boundary

Stage 8 implements evidence storage, lineage, structured claims, deterministic verifiers,
verification persistence, audit, and the completion gate.

Stage 9 adds the production deterministic Report Tool, a common strong report model, PDF/JSON
renderers, stricter atomic Artifact persistence, and report consistency checks. The final
verification gate remains after Artifact generation. DOCX, XLSX, Markdown, HTML, LangGraph, an LLM
planner, and an LLM verifier remain outside the frozen v1.0 implementation.
