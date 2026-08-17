# Supplier Quality Report Readability Audit

## Scope and authority

This audit was completed before changing the report renderer. It covers the frozen Supplier
Quality Analysis v1.1 design, the report model/composer/renderers, Artifact persistence, Analytics,
Evidence, deterministic verifiers, workflow input construction, report tests, and the real PDF
generated from the current synthetic TENANT-DEMO enterprise dataset.

The frozen design remains authoritative. In particular, `quality_metrics.v1` only provides
`defect_count`, `inspected_count`, `defect_rate`, and `period_over_period_trend`. This work must not
add quarter aggregation, ranking, threshold classification, action priority, or root-cause
inference in the report layer.

## Audited implementation chain

```text
TaskContract.constraints
  -> StepInputBuilder (Q2 dates and supplier scope)
  -> database_query (read-only registered query, Database Evidence)
  -> analysis_engine (four frozen metrics, Calculation Evidence)
  -> ReportComposer (single ReportDocument for JSON and PDF)
  -> ReportValidator (scope, Evidence, formula, lineage, exact metric identity)
  -> PdfReportRenderer / JsonReportRenderer
  -> Artifact Repository (immutable bytes, size, checksum, Evidence IDs)
  -> WorkflowVerifier
       -> ArtifactIntegrityVerifier
       -> DeliverableVerifier
       -> CitationVerifier
       -> NumericVerifier
       -> SafetyVerifier
```

The PDF carries the same canonical `ReportDocument` as JSON in a non-rendered marker. The
workflow verifier reads that model rather than attempting to infer claims from PDF prose. Numeric
claims continue to come from `analysis_results.metrics`; formatting text is not a numeric source.

## Baseline PDF reviewed

The current code was run against the local synthetic enterprise database containing 5,000
inspection records and 15 TENANT-DEMO suppliers. The request was:

```text
Analyze supplier quality for Q2 2026, compare with Q1, check the quality policy,
and generate a PDF report.
```

The workflow completed and its independent verification status was `PASSED`. The generated
baseline PDF had these measured properties:

| Measure | Baseline |
|---|---:|
| Total pages | 14 |
| Management/main-body pages before Evidence | 13 |
| Pages occupied by the raw Key Metrics table | 9 (pages 2-10) |
| Raw metric observations | 180 |
| Defect-rate findings | 45 |
| Repeated first-period warnings | 15 |
| Evidence/trace pages | 1 |

Rendered page inspection confirmed that the PDF is technically readable and does not overlap,
but is structured as an engineering dump: page 1 leads with system metadata, pages 2-10 expose raw
metric rows, pages 11-13 repeat defect-rate observations and warnings, and Evidence plus execution
trace are compressed into the final page.

## Findings and root causes

### 1. Incorrect `0 authorized supplier(s)`

An empty `TaskContract.constraints.supplier_ids` means the approved tenant-wide supplier scope.
The database query correctly omits a supplier filter and returned 45 Q2 supplier-month rows for 15
suppliers. `ReportComposer._executive_summary`, however, used
`len(request.scope.supplier_ids)`, converting the empty sentinel into the false business statement
"0 authorized supplier(s)."

The safe presentation source is the existing supplier dimensions in Calculation Evidence. This is
presentation grouping, not supplier discovery or a new business calculation. The contract's empty
scope remains an authorized resolved-scope sentinel and is not rewritten.

### 2. Raw metrics dominate the main report

`ReportDocument.key_metrics` correctly preserves the complete Analytics output, but the PDF
renderer prints every item in a single `Key Metrics` table before any management interpretation.
The renderer does not distinguish management content from audit detail.

### 3. Major Findings are metric serialization

`ReportComposer._findings` converts every `defect_rate` metric into a separate finding. With 15
suppliers and three months, this creates 45 repeated "Reported defect rate" statements. These are
valid numeric observations but are not findings in the management-report sense.

### 4. Analytics notes are mislabeled as business risk

Analytics intentionally emits one warning per supplier for the first period because no prior month
exists. `ReportComposer._risks` maps every warning to `REVIEW_REQUIRED`, producing 15 apparent
business risks. The warning is a shared methodology limitation and should be stated once.

### 5. Q1 versus Q2 is not calculated

`StepInputBuilder` sends only the Q2 start/end dates to `database_query`. The returned dataset and
Calculation Evidence therefore contain April-June data only. The trend metric compares adjacent
months inside Q2. It is not a Q1-versus-Q2 calculation. The current report fails to make this
limitation prominent.

### 6. Supplier ranking is unavailable

The report correctly leaves `supplier_ranking` empty because the frozen Analytics contract has no
ranking operation. The PDF nevertheless dedicates a main-body heading to the absence. This belongs
in limitations, not in prime management space.

### 7. `Verification PENDING` is technically correct but poorly placed

The frozen lifecycle is Report Artifact creation followed by independent verification. The
render-time model must remain `PENDING`; hard-coding `PASSED` would be false. Displaying `PENDING`
beside the report title, however, makes a successfully completed downloaded report appear
unverified. The final status belongs in task/Artifact metadata. The PDF should explain the
render-time lifecycle in Methodology and Appendix metadata.

### 8. Execution trace is too prominent relative to management content

Execution trace is valuable audit data but shares the only Evidence page and uses full identifiers.
It should be retained in a dedicated appendix after management and analytical content.

### 9. Long identifiers harm table layout

Task, Trace, Evidence, ToolCall, query, and checksum identifiers are displayed in full throughout
the main body. They wrap unpredictably and increase row height. Management pages can use clearly
abbreviated identifiers while appendices and the canonical model retain exact values.

### 10. Pagination follows raw flow rather than information hierarchy

The renderer uses only one forced page break, then allows large tables and repeated paragraphs to
drive pagination. This yields nine raw-metric pages and no guaranteed first-five-page management
layer. Repeating table headers work, but section ownership and page budgeting do not.

### 11. The report input does not carry the workflow Trace ID

The frozen `report_generator.v1` input contains `task_id` but not the workflow `trace_id`.
`ReportComposer` therefore uses the Task ID as the task-scoped trace key in the canonical report
model. The PDF must not label that value as the workflow Trace ID. Appendix C instead directs
readers to Task metadata, where the real workflow Trace ID and final verification result are
stored. Adding `trace_id` to the report input would require an explicit frozen-design change.

## Contracts that must remain unchanged

- Report input/output tool schemas and `supplier_quality_report.v1` template identifier.
- The canonical `ReportDocument` fields consumed by JSON and verification.
- Exact `analysis_results.metrics` and `key_metrics` identity with Calculation Evidence.
- `EvidenceType` limited to DOCUMENT, DATABASE, and CALCULATION.
- Database query fingerprint, dataset checksum, formulas, operands, units, precision, and input
  Evidence lineage.
- Artifact type, immutable location, checksum, size, generator version, and Evidence IDs.
- Post-render verification lifecycle and the Numeric, Citation, Deliverable, Safety, and Artifact
  gates.

## Audit conclusion

The readability failure is caused by presentation mapping and information architecture, not by
missing audit data. The safe change is to retain the same authoritative model and Evidence while
rendering it in three layers: management summary, analytical overview, then audit appendices.
No new business calculation is required or authorized for this optimization.

## Measured result after optimization

The optimized report was regenerated through the real CLI workflow against the same local
synthetic TENANT-DEMO database. The workflow completed and independent verification returned
`PASSED`.

| Measure | Baseline | Optimized |
|---|---:|---:|
| Total pages | 14 | 11 |
| Management/main-body pages | 13 | 5 |
| Pages occupied by raw metrics | 9 | 4 (Appendix A) |
| Raw metric observations retained | 180 | 180 |
| Management findings | 45 | 3 |
| Repeated first-period warnings | 15 | 0 |
| Dedicated Evidence/lineage pages | 1 shared page | 1 (Appendix B) |
| Dedicated execution-trace pages | 1 shared page | 1 (Appendix C) |

The first five pages now form a bounded management layer, while every raw metric, operand,
formula, full Evidence identifier, query fingerprint, checksum, and execution record remains in
the appendices or embedded canonical model. The empty supplier-scope sentinel is presented as 15
represented suppliers, based only on existing Calculation Evidence dimensions. Q1-versus-Q2,
ranking, root cause, threshold classification, and action priority remain explicitly unavailable.

## Frontend browser E2E result

The repository has a frontend report route, so the final check continued through the local browser
E2E surface. A governed PDF task reached `COMPLETED`, the `Report (1)` navigation item became active,
the `Verified Artifacts` region exposed the PDF metadata, and the download link emitted a browser
download event. The report page scrolled from the top to its 193-pixel maximum offset without
losing the active navigation state, had no horizontal overflow at a 1280 x 720 viewport, and
produced no browser console warnings or errors. The report route defines no filter controls, so
filter behavior is not applicable to this page.
