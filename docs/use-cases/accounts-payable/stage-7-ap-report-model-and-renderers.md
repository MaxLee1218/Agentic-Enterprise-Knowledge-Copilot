# Stage 7 — AP Report Model and JSON/PDF Renderers

**Status:** `COMPLETE — 2026-08-23`  
**Report model:** `accounts_payable_report_model.v1`  
**Template:** `accounts_payable_report.v1`  
**Generator:** `report_generator.v2`  
**Execution boundary:** UC2 remains disabled; Stage 8 understanding, planning, input construction
and shared-Graph integration are not implemented.

## Delivered scope

Stage 7 adds an Accounts Payable profile behind the existing `report_generator` capability name.
It reuses the current Evidence reader, Output Guard, Artifact repository, atomic writer, Artifact
metadata, checksum and workflow parser boundaries. It does not replace or reinterpret the frozen
Supplier Quality report profile.

- `APReportRequestV1` binds Task ID, exact AP scope, the deterministic exception-summary result,
  all current-task Evidence references, the version-bound policy rule snapshot, output format,
  language and trusted `AGGREGATE | DETAIL` disclosure mode.
- `AccountsPayableReportV1` is the single strong source model for JSON and PDF. It contains the
  frozen management sections, explicit structured claims, Evidence index, execution trace and
  version metadata with `verification_status=PENDING`.
- `AccountsPayableReportComposer` reconstructs complete AP Calculation Evidence batches, requires
  exactly one exception-summary and one supplier-rate run, copies canonical Decimal/count/rate/day
  values, and never queries or recalculates business results.
- `APJsonReportRenderer` and `APPdfReportRenderer` derive from the same canonical model. The PDF
  carries the canonical model for an independent round trip; equal models produce equal bytes.
- `AccountsPayableReportValidator` checks Task/scope/detail identity, Evidence type coverage,
  calculation reconstruction, summary identity, aggregate detail exclusion, format round trip,
  Artifact type/location/checksum/size and readability.
- `AccountsPayableReportTool` enforces the AP purpose and detail scope, applies the stricter AP
  restricted-field block, enforces 25 MiB JSON and 15 MiB PDF limits, and commits one tenant-owned
  immutable AP Artifact through the existing atomic repository.
- The shared Artifact repository and report parser now recognize
  `ACCOUNTS_PAYABLE_REPORT_JSON` and `ACCOUNTS_PAYABLE_REPORT_PDF` without changing existing
  Supplier Quality filenames, media types, parsers or Artifact enums.

## Canonical report behavior

Aggregate mode excludes record-level duplicate, PO, payment and material-detail rows and does not
emit invoice-record dimensions in numeric claims. Detail mode exposes only opaque invoice record
keys and an allowlisted subset of deterministic observed/threshold values. It never exposes raw
invoice/PO numbers, payment references, internal account numbers, tax IDs, bank accounts, IBAN or
SWIFT values.

Management detail is deterministically ordered and capped at 100 rows. Complete exception counts,
per-currency amounts and lineage remain in Calculation Evidence. The report preserves currencies as
separate dimensions and performs no currency conversion. Empty data says no invoice records were
available; it does not claim that no compliance issue exists.

Numeric claims are emitted only for the allowlisted Stage 6 baselines. Counts stay integers; money
stays four-place Decimal with currency; ratios stay eight-place canonical values; day counts stay
integers; average days preserve the analytics precision. Policy-governed claims retain exact rule
IDs and complete Calculation/Database/Document lineage.

## Security and failure posture

- AP report execution requires purpose `accounts_payable_analysis.v1`; detail output additionally
  requires `finance:ap.detail`.
- `detail_access` is a trusted policy input, not narrative or model authority.
- Any sensitive-field alias recognized by the shared registry is an AP report error before shared
  redaction; no bank/tax/reference field is repaired into an otherwise successful AP report.
- Wrong-task Evidence, missing/incomplete calculation batches, summary drift, missing source types,
  unsupported format, corrupt rendered bytes, oversize output and post-commit inconsistency fail
  with the existing typed report errors.
- The tool is idempotent over the final report/profile/summary/Evidence/format/detail input and has
  no business-data mutation or external-publication side effect.

## Verification and regression coverage

The Stage 7 suite covers:

- strict request validation and exact summary/policy binding;
- canonical composer values and aggregate/detail disclosure isolation;
- Stage 6 structured claim-adapter compatibility;
- deterministic JSON and PDF model round trips;
- identical canonical values across formats;
- AP Artifact enum, filename, parser, checksum, size, tenant ownership and idempotency;
- AP purpose and detail-scope denial;
- restricted financial field blocking before persistence;
- corrupt JSON/PDF parser failure and aggregate detail-injection rejection;
- unchanged Supplier Quality reporting, verifier and Artifact repository regressions.

## Deferred work

Stage 7 deliberately does not enable `ACCOUNTS_PAYABLE_MANIFEST.execution_enabled`. AP Task
Understanding, Plan generation/validation, report input construction, policy/approval pause and
shared Graph execution remain Stage 8. API/frontend exposure remains Stage 9; evaluation,
performance and release evidence remain Stages 10–12. No external report delivery or finance
business action is introduced.
