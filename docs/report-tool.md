# Deterministic Report Tool

## Scope and frozen compatibility

`report_generator` is the production deterministic report adapter for
`supplier_quality_analysis.v1`. The frozen v1.0 contract in
[`design/tool_contract.md`](design/tool_contract.md) permits `PDF` and `JSON` only. Markdown,
HTML, uploaded templates, model-written prose, external publication, and report approvals are
outside this baseline.

The tool accepts the frozen scope, Analytics result, Evidence IDs, template version, format, and
language. It resolves Evidence only inside the current Task, builds one strong `ReportDocument`,
checks Evidence/query/formula/lineage/metric consistency, renders PDF or JSON from the same model,
and atomically commits an immutable Artifact.

It does not retrieve documents, execute SQL, recalculate metrics, infer root causes, mutate
Evidence, change Task state, or bypass the independent final Verifier.

## Report model and rendering

`ReportDocument` contains identity and execution metadata, executive summary, authorized scope,
controlled-document references, database coverage and query fingerprints, exact Analytics
metrics, deterministic findings, bounded risk statements, fixed-rule actions, limitations,
Evidence checksums and lineage, and a safe execution trace.

The model schema is `supplier_quality_report_model.v1`; the frozen presentation version remains
`supplier_quality_report.v1`. The offline workflow has no separate distributed trace identifier,
so it records the Task ID as its trace correlation ID without adding a field to the frozen Tool
input.

Supplier ranking is explicitly empty and accompanied by `RANKING_NOT_AVAILABLE`.
`quality_metrics.v1` does not define ranking, and the Report Tool must not create a new analytical
result.

JSON uses stable UTF-8 serialization with sorted keys, enum/UTC encoding, and `allow_nan=false`.
PDF uses ReportLab with a local CID font, page headers, page numbers, and a layered management
layout. The first five pages contain executive summary, supplier-month overview, controlled policy
context, findings/actions, and methodology/limitations. Raw metrics, full Evidence/lineage, and
execution trace remain in appendices. Ratios are formatted as percentages and ratio deltas as
percentage points without changing canonical values. It has no JavaScript or external network
dependency. A canonical JSON representation is carried in a non-rendered PDF comment so the
independent Verifier can validate the exact model without parsing report prose.

An empty contract `supplier_ids` list remains the authorized resolved-scope sentinel. Management
display coverage uses existing supplier dimensions in Calculation Evidence and never interprets
that sentinel as zero suppliers. Supplier order is alphanumeric, not a risk ranking.

## Artifact persistence and integrity

`LocalArtifactRepository` delegates bytes to `AtomicArtifactWriter`:

1. validate one safe filename component, extension, non-empty content, and configured size limit;
2. write a temporary file under `ARTIFACT_DIR`;
3. flush and `fsync`;
4. atomically replace the final path;
5. reread and verify bytes, size, and SHA-256;
6. save frozen `Artifact` metadata.

Metadata includes Artifact ID, Task ID, type, location, media type, SHA-256, byte size, generator
version, cited Evidence IDs, and UTC creation time. If metadata persistence fails, the newly
written file is deleted. Queries support ID lookup, Task listing, and existence checks.
`REPORT_MAX_SIZE_BYTES` defaults to 10 MiB and is validated by `Settings`.

## Validation and Evidence

Pre-render validation requires current-Task Document, Database, and Calculation Evidence.
Database Evidence needs a query fingerprint. Calculation Evidence needs a formula and lineage to
referenced Database Evidence. Its metrics must exactly equal the Analytics output; NaN and
Infinity are rejected.

Post-render validation round-trips JSON or the PDF-carried model into `ReportDocument`. Artifact
validation checks type, extension, path boundary, bytes, size, and SHA-256. The workflow then runs
the independent frozen Evidence, citation, numeric, safety, deliverable, and Artifact gate.

The Report Tool does not create a fourth Evidence type. The frozen enum contains only `DOCUMENT`,
`DATABASE`, and `CALCULATION`, and the walkthrough records Artifact generation in audit while the
Artifact cites upstream Evidence. The ToolResult therefore has no newly registered Evidence IDs.

## Failure mapping

| Condition | Stable error |
|---|---|
| Unsupported format or template | `REPORT_FORMAT_UNSUPPORTED` |
| Missing or malformed input/Evidence/query/formula | `REPORT_INPUT_INVALID` |
| Cross-Task Evidence or Task mismatch | `REPORT_INPUT_DENIED` |
| Renderer or Artifact persistence failure | `REPORT_GENERATION_FAILURE` |
| Executor deadline | `REPORT_TIMEOUT` / `TOOL_TIMEOUT` |

No raw stack trace, report body, SQL, token, or secret is logged.
