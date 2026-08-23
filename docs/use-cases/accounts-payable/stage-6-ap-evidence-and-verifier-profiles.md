# Stage 6 — AP Evidence and Verifier Profiles

**Status:** `COMPLETE — 2026-08-23`  
**Verifier profile:** `accounts_payable_verifier.v1`  
**Execution boundary:** UC2 remains disabled; Stage 7 report rendering and Stage 8 workflow
integration are not implemented.

## Delivered scope

Stage 6 adds the frozen Accounts Payable verification profile to the existing deterministic
verification framework. It does not create a second pipeline and it does not parse narrative text.

- `APReportClaimV1` defines the explicit report claim envelope. The adapter reads only structured
  `evidence.claims` values and maps percentage display values back to canonical ratios.
- `VerifierProfileV1` binds a domain Task type to exact table, column, query-template and sensitive
  field allowlists.
- `APEvidenceMetadataVerifier` validates AP Document, Database and Calculation Evidence metadata,
  checks content/run checksums, reconstructs every batch, and rejects incomplete or drifted runs.
- `APPolicyBindingVerifier` binds Calculation Evidence and policy-governed claims to the Task rule
  set/manifest plus exact Document rule IDs and Database lineage.
- `APConsistencyVerifier` enforces requested-operation coverage, successful-step ownership,
  non-empty eligibility, record-level Evidence links and supplier/entity/unit/currency scope.
- `APNumericVerifier` resolves a claim to exactly one complete operation run and compares counts,
  money, ratios and day values with deterministic Decimal/null semantics.
- `SafetyVerifier` can consume a code-owned profile. AP checks use only the AP schema/templates and
  treat restricted fields as errors. The sensitive registry now covers `swift`, `tax_id`,
  `payment_reference`, and `internal_account_number` aliases.
- The existing Supplier Quality Composite order remains unchanged.

## Composite matrix

| Order | Supplier Quality v1.1 | Accounts Payable v1 |
|---:|---|---|
| 1 | `EvidenceStructureVerifier` | `EvidenceStructureVerifier` |
| 2 | `DeliverableVerifier` | `APEvidenceMetadataVerifier` |
| 3 | `CitationVerifier` | `DeliverableVerifier` |
| 4 | `NumericVerifier` | `CitationVerifier` |
| 5 | `SafetyVerifier` | `APPolicyBindingVerifier` |
| 6 | — | `APConsistencyVerifier` |
| 7 | — | `APNumericVerifier` |
| 8 | — | `SafetyVerifier` with AP profile |

The AP sequence is created by `composite_verifier_for_profile()` using the manifest profile ID.
The Supplier Quality factory returns the original five-verifier Composite unchanged.

## Evidence gates

### Document Evidence

Verification requires stable document/version/location, collection and index snapshots, effective
dates covering the Task range, classification, excerpt checksum, retrieval score/trace, rule-set
version and non-empty bound rule IDs. The excerpt checksum must equal the Evidence content checksum.

### Database Evidence

Verification requires a stable query ID/fingerprint, frozen template/version, AP schema snapshot,
read-only `SELECT` proof, sorted table/column metadata, exact Task snapshot and hashed tenant/time/
supplier/entity/unit/currency scope, row/empty/truncation state and dataset checksum. Truncation is
always an error.

### Calculation Evidence

Verification requires the frozen operation/engine/formula catalogue, normalization/precision/
rounding versions, exact rule ID/version bindings, Task rule manifest checksum, input checksums,
run ID and complete zero-based batches. The verifier reconstructs `APAnalyticsResultV1`, validates
its output checksum, and proves its input checksums against Database or parent Calculation Evidence.

## Numeric baseline policy

- counts and record day counts: exact integers;
- money and signed money: exact four-place Decimal plus one explicit currency dimension;
- ratios: exact canonical eight-place Decimal;
- average days: exact two-place Decimal;
- null: must match null; missing, non-finite or ambiguous values fail;
- every claim: exactly one complete Calculation run and one allowlisted metric location.

The allowlist covers duplicate groups/count/exposure, unique exception counts/rates and amounts,
PO variance amounts/rates, late/early days, overpayment, and per-supplier counts/rates/amounts.
No currency conversion or cross-currency total is accepted.

## Tamper and regression coverage

The Stage 6 suite covers:

- missing/wrong Evidence references and dual-lineage failures;
- duplicate or missing numeric baselines;
- numeric unit, precision, value and currency mismatch;
- Task rule manifest and rule-version mismatch;
- requested-operation, step-result and record-scope mismatch;
- truncated Database Evidence and incomplete Calculation batches;
- restricted AP output fields;
- profile routing and unchanged Supplier Quality verifier order.

All Stage 6 problems above are `ERROR`; none are downgraded to warnings.

## Deferred work

The following remain deliberately outside Stage 6:

- `AccountsPayableReportV1`, JSON/PDF renderers, Artifact parsing and golden reports (Stage 7);
- AP understanding, planning, graph/runtime registration and manifest enablement (Stage 8);
- API/UI enablement, end-to-end evaluation and release gates (Stages 9–11).

`ACCOUNTS_PAYABLE_MANIFEST.execution_enabled` therefore remains `false` and the AP analytics/report
tools are not registered into the executable Supplier Quality container.
