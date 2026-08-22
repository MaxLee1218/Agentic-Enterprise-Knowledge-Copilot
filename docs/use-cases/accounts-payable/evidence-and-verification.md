# Evidence, Claim Lineage and Verification

## 1. Evidence model reuse

UC2 reuses the existing `DOCUMENT`, `DATABASE`, and `CALCULATION` Evidence types. Invoice,
payment and PO facts are DATABASE Evidence; deterministic exception and KPI results are
CALCULATION Evidence. No `INVOICE_EVIDENCE` or report Evidence type is added.

The existing immutable envelope (`evidence_id`, Task/Step/ToolCall identity, type, source
reference, minimized classified content, checksum and UTC timestamp) remains sufficient. AP adds
profile-specific validated metadata inside the current typed source/content objects.

## 2. Source metadata

### Document Evidence

Required AP document source metadata:

```text
document_id, document_version, chunk_id or page,
collection_id, index_snapshot_id, effective_from/effective_to,
classification, excerpt_checksum, retrieval score/trace ID,
policy_rule_set_version, bound_rule_ids
```

The content holds only the minimum policy excerpt necessary to support the claim. Retrieved text
is untrusted data and cannot define executable thresholds unless an exact controlled rule binding
also resolves.

### Database Evidence

Required AP database source metadata:

```text
query_id, query_fingerprint, query_template_id/template_version,
schema_version/schema_snapshot, statement_type=SELECT, read_only=true,
sorted table_names/column_names, snapshot_at,
tenant scope hash, supplier/legal-entity/business-unit/time/currency scope hashes,
row_count, truncated, dataset_checksum
```

Raw SQL, credentials, connection strings, bank data, payment reference and unrestricted row
payloads are forbidden. Detailed fact content uses opaque invoice/PO/payment record keys and only
columns required by the operation.

### Calculation Evidence

Required AP calculation source metadata:

```text
operation_name/version, engine_version, formula,
normalization/precision/rounding versions,
rule IDs/versions, rule_set_version and manifest_checksum,
input Evidence IDs and dataset checksums,
batch index/count and calculation-run ID
```

Content holds typed exception records, metrics, eligibility/exclusion coverage and warnings.
Every CALCULATION item must trace to DATABASE Evidence. Policy-governed results must also trace to
the bound DOCUMENT Evidence through the rule snapshot; a code-only threshold is insufficient.

## 3. Claim-level lineage

```text
Numeric/data report claim
  -> summary or detection CALCULATION Evidence
  -> one or more DATABASE Evidence items

Policy report claim
  -> DOCUMENT Evidence

Policy-governed exception claim
  -> CALCULATION Evidence
      -> DATABASE Evidence
      -> rule snapshot -> DOCUMENT Evidence
```

Example: “17 invoices exceeded the permitted PO variance” cites summary CALCULATION Evidence whose
17 record keys resolve to variance batches and database inputs; its 5% policy statement cites the
exact policy document version and controlled rule binding. Citation IDs in the report are stable
Evidence IDs, never a prose bibliography alone.

## 4. Verifier reuse matrix

| Verifier | Reuse | AP extension |
|---|---|---|
| `ArtifactIntegrityVerifier` | reusable with profile | accept AP Artifact types/report parser; keep ownership, type, size, checksum, readability and citation coverage checks |
| `EvidenceStructureVerifier` | reusable with profile | validate AP query/rule/operation metadata and Calculation-to-Database/Document lineage |
| `DeliverableVerifier` | reusable | use AP manifest required section identifiers rather than Supplier-only identifiers |
| `CitationVerifier` | fully reusable core | add policy-governed exception rule requiring both Calculation/Database and Document lineage |
| `NumericVerifier` | reusable with AP adapter | resolve AP operation metric baselines, Decimal units and per-currency dimensions |
| `SafetyVerifier` | reusable with domain allowlists | use AP tables/columns/templates, AP roles/purpose, sensitive-field policy and read-only proof |
| AP consistency verifier | new domain rule set | validate invoice/PO/payment tenant, scope, supplier, entity, unit, currency and cardinality relationships |
| AP policy binding verifier | new domain rule set | prove active rule manifest checksum and exact policy document bindings |

The `CompositeVerifier` remains one framework. Domain verifiers are selected by the manifest; no
second AP verification pipeline is created.

## 5. Numeric verification requirements

The report-to-Evidence mapper creates structured `NumericClaim` values for:

- duplicate group/count and duplicate exposure by currency;
- total unique exception invoice count and exception rate;
- invoice and exception amount by currency;
- signed/absolute PO variance amount and variance rate;
- late/early days and average days late;
- overpayment amount;
- per-supplier invoice count, exception count and exception rate.

Counts require exact integer equality. Money requires exact Decimal equality at four stored
places and the same currency dimension. Ratios require the operation's eight-place canonical
precision; display percentages are mapped back to canonical ratios, not compared as free text.
Day counts are exact integers. Null must match null; missing, NaN and infinity fail.

The Verifier never asks the LLM or report renderer to recompute a baseline. It compares the
structured report claim with exactly one uniquely resolved Calculation Evidence baseline.

## 6. Coverage and empty behavior

Verification fails if:

- a requested detection has no step/result/Evidence;
- a non-empty source population has zero eligible coverage without an explicit operation failure;
- a query or exception result was truncated;
- excluded records are omitted from coverage/limitations;
- a policy comparison lacks the exact document/rule binding;
- counts cannot be traced to all deterministic result batches;
- currencies are aggregated without partitioning;
- an invalid Artifact or material numeric/citation mismatch remains.

A truly empty invoice population may complete with an empty report if DATABASE Evidence proves
the scope, all operations return explicit empty results, and the report says “no invoice records
were available”; it may not say “no compliance issues exist.”

## 7. Report contract verification

Before `COMPLETED`, verification proves:

1. report time/supplier/entity/unit/currency scope equals the Task Contract;
2. report schema/template/generator/rule versions equal the bound Plan inputs;
3. every required section exists and was produced by the successful report step;
4. each material finding has compatible Evidence and each policy statement has Document Evidence;
5. every canonical numeric claim matches Calculation Evidence;
6. exclusions and limitations are complete;
7. only authorized detail level is present;
8. the Artifact is one current-task governed JSON/PDF file with matching size/checksum;
9. policy/approval scope covers the final resolved arguments;
10. all business-database activity proves allowlisted read-only SELECT behavior.

Warnings may complete only when they describe frozen business exclusions or nonmaterial coverage
limits. Tenant/scope mismatch, restricted-field exposure, policy binding mismatch, truncation,
unsupported arithmetic, missing lineage, or numeric mismatch is an error.
