# Supplier Quality Management Report Design

## Design objective

The PDF uses one authoritative `ReportDocument` but presents it in three reading layers:

```text
Management layer (pages 1-5)
  -> Analytical overview (supplier-month defect rates and existing monthly deltas)
  -> Audit layer (raw metrics, Evidence/lineage, execution trace)
```

This is a presentation design. It does not change the frozen tool contract or introduce a second
analytics path.

## Main report page architecture

### Page 1 - Executive Summary

- Report title and quarter.
- Compact period, resolved supplier coverage, generated time, and shortened Task ID.
- Four presentation metadata cards: analysis period, suppliers represented in Calculation
  Evidence, observed months, and Evidence-item count.
- Deterministic executive summary.
- At most five controlled observations.
- One fixed policy-backed recommended focus.

The page does not display raw metric-observation count as a business KPI and does not show
render-time `PENDING` as if it were the final task result.

### Page 2 - Supplier Quality Overview

- One supplier per row.
- One column per observed Q2 month using existing `defect_rate` values.
- Latest available existing `period_over_period_trend` value.
- Ratio presentation as percent and ratio-delta presentation as percentage points.
- An alphanumeric, non-ranking dot plot sourced from the same existing defect-rate values.

The renderer pivots and formats observations but does not sum counts, calculate an overall rate,
rank suppliers, or assign improved/worsened semantics.

### Page 3 - Applicable Quality Policies

- Structured document/version/topic/location/Evidence summary.
- Short relevant excerpts.
- Database coverage summary.
- Explicit statement that threshold compliance classification is unavailable without a
  deterministic controlled rule.

Full retrieved excerpts remain in Appendix B.

### Page 4 - Findings and Recommended Actions

- Up to eight deterministic findings, expressed as coverage and interpretation statements rather
  than one finding per metric.
- Business-risk boundary stating that Analytics notes are not risk classifications.
- Fixed policy-backed action with no invented HIGH/MEDIUM/LOW priority.

### Page 5 - Methodology and Limitations

- Controlled formulas copied from Calculation Evidence metadata.
- Percent, ratio, and percentage-point display rules.
- First-period trend note stated once.
- Ranking, Q1-versus-Q2, root cause, and policy classification limitations.
- Explanation that final verification runs after Artifact creation.

## Appendices

### Appendix A - Detailed Calculation Metrics

Retains every canonical metric with:

- metric name;
- supplier and period dimensions;
- raw value;
- raw unit;
- numerator and denominator.

No percent scaling is applied in this appendix.

### Appendix B - Evidence and Lineage

Retains full policy excerpts, Database Evidence coverage, query identifiers, Calculation formulas,
input Evidence IDs, checksums, and source type.

### Appendix C - Execution Trace

Retains complete Step, ToolCall, and Evidence identifiers plus render metadata. Long identifiers
are wrapped for display only; the embedded report model and Artifact metadata preserve exact
values. The frozen `report_generator.v1` input does not carry the workflow Trace ID, so the PDF
states that the workflow trace is stored with Task metadata instead of presenting the Task ID as
if it were the Trace ID.

## Presentation formatting rules

| Canonical unit | Example raw value | Display |
|---|---:|---:|
| `count` | `18994` | `18,994` |
| `ratio` | `0.0587` | `5.87%` |
| `ratio_delta` | `0.0018` | `+0.18 pp` |

Formatting never mutates `key_metrics` or `analysis_results`. Numeric verification continues to
read the canonical raw values and units.

## Deterministic content rules

- Supplier coverage comes from existing supplier dimensions in Calculation Evidence, with the
  empty contract supplier list still labeled as an authorized resolved scope.
- Supplier ordering is alphanumeric and explicitly not a risk ranking.
- The first-period trend warning is consolidated into one methodology note.
- Policy excerpts may be shortened on management pages but are complete in Appendix B.
- Main-page IDs may be abbreviated with `...`; full values remain in appendices and the model.
- `verification_status=PENDING` remains truthful in the render-time model. The PDF explains that
  final verification status is stored outside the pre-verification Artifact.

## Analytics boundary

No new business calculation was added.

Specifically, this design does not provide:

- total inspected quantity or total defects across suppliers;
- overall Q2 defect rate;
- Q1-versus-Q2 change;
- supplier ranking;
- threshold-based policy compliance or risk classification;
- root-cause inference;
- action priority.

Adding any of these requires an approved frozen-design change followed by Analytics contract,
Calculation Evidence, Numeric Verifier, tests, and report-model support.

## JSON and verification compatibility

Both PDF and JSON continue to use `supplier_quality_report_model.v1`. No field is removed or
renamed. The PDF's visual grouping does not become an authoritative claim source. The canonical
model remains embedded for round-trip validation, and all existing workflow verification gates
remain after Artifact creation. ReportLab invariant mode makes repeated rendering of the same
canonical model byte-deterministic, which supports the frozen idempotency contract and prevents
binary-scanner behavior from depending on volatile internal PDF identifiers. Page-stream
compression is disabled so the unchanged output guard scans readable PDF instructions instead of
compressed binary sequences that can accidentally resemble an internal path.
