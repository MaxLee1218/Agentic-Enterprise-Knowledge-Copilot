"""Deterministic JSON and management-oriented PDF report renderers."""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol, cast
from xml.sax.saxutils import escape

import reportlab
from pydantic import JsonValue
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import TimeStamp
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from copilot.contracts.base import JsonMapping
from copilot.contracts.enums import ReportLanguage
from copilot.tools.reporting.chart_builder import supplier_defect_rate_chart
from copilot.tools.reporting.exceptions import (
    ReportRenderingError,
    UnsupportedReportFormatError,
)
from copilot.tools.reporting.presentation import (
    format_metric_value,
    observed_periods,
    observed_supplier_ids,
    period_label,
    raw_metric_value,
    short_identifier,
    supplier_overview_rows,
    wrap_identifier,
)
from copilot.tools.reporting.schemas import ReportDocument, ReportFormat

PDF_MODEL_MARKER = b"%COPILOT_REPORT_MODEL:"
_DARK_BLUE = colors.HexColor("#123B5D")
_MID_BLUE = colors.HexColor("#2F6B8A")
_TEXT = colors.HexColor("#263238")
_MUTED = colors.HexColor("#607D8B")
_LIGHT = colors.HexColor("#F4F7F9")
_BORDER = colors.HexColor("#B8C5CC")


@dataclass(frozen=True, slots=True)
class RenderedReport:
    """Validated renderer output before governed persistence."""

    content: bytes
    media_type: str
    extension: str


@dataclass(frozen=True, slots=True)
class _ReportStyles:
    title: ParagraphStyle
    subtitle: ParagraphStyle
    heading: ParagraphStyle
    subheading: ParagraphStyle
    body: ParagraphStyle
    compact: ParagraphStyle
    caption: ParagraphStyle
    table_body: ParagraphStyle
    appendix_title: ParagraphStyle


class ReportRenderer(Protocol):
    """Pure renderer interface."""

    format: ReportFormat

    def render(self, document: ReportDocument) -> RenderedReport:
        """Render a ReportDocument without writing files."""
        ...


def canonical_report_json(document: ReportDocument, *, pretty: bool) -> bytes:
    """Serialize with stable enum/time encoding and strict finite-number handling."""
    value = cast(JsonMapping, document.model_dump(mode="json"))
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class JsonReportRenderer:
    """Stable JSON renderer independent of presentation templates."""

    format = ReportFormat.JSON

    def render(self, document: ReportDocument) -> RenderedReport:
        try:
            content = canonical_report_json(document, pretty=True)
        except (TypeError, ValueError) as exc:
            raise ReportRenderingError() from exc
        return RenderedReport(
            content=content,
            media_type="application/json",
            extension=".json",
        )


class PdfReportRenderer:
    """Layered management, analytical, and audit PDF from one canonical report model."""

    format = ReportFormat.PDF

    def render(self, document: ReportDocument) -> RenderedReport:
        try:
            content = self._render_pdf(document)
            model = base64.b64encode(canonical_report_json(document, pretty=False))
            content += b"\n" + PDF_MODEL_MARKER + model + b"\n"
        except Exception as exc:
            raise ReportRenderingError() from exc
        return RenderedReport(
            content=content,
            media_type="application/pdf",
            extension=".pdf",
        )

    @staticmethod
    def _render_pdf(document: ReportDocument) -> bytes:
        font_name = _register_report_font(document.execution_metadata.language)
        styles = _build_styles(font_name)
        buffer = BytesIO()
        doc = BaseDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=16 * mm,
            rightMargin=16 * mm,
            topMargin=18 * mm,
            bottomMargin=17 * mm,
            title=document.title,
            author="Agentic Enterprise Knowledge Copilot",
            subject="Governed Supplier Quality Analysis",
            invariant=True,
            pageCompression=0,
        )
        frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
        doc.addPageTemplates(
            PageTemplate(id="report", frames=[frame], onPage=_draw_page_header_footer)
        )
        story = _management_pages(document, styles, doc.width)
        story.extend(_appendices(document, styles, doc.width))
        doc.build(story, canvasmaker=_canvas_factory(document.execution_metadata.generated_at))
        return buffer.getvalue()


class RendererRegistry:
    """Closed renderer map that rejects unknown formats before rendering."""

    def __init__(self, renderers: tuple[ReportRenderer, ...] | None = None) -> None:
        selected = renderers or (JsonReportRenderer(), PdfReportRenderer())
        self._renderers = {renderer.format: renderer for renderer in selected}

    def get(self, report_format: ReportFormat) -> ReportRenderer:
        """Resolve an allowlisted renderer."""
        try:
            return self._renderers[report_format]
        except KeyError as exc:
            raise UnsupportedReportFormatError() from exc


def _canvas_factory(generated_at: datetime) -> Any:
    """Build invariant PDF objects with canonical report time in document metadata."""

    def factory(filename: Any, **kwargs: Any) -> Canvas:
        canvas = Canvas(filename, **kwargs)
        timestamp = TimeStamp(invariant=True)
        timestamp.t = generated_at.timestamp()
        timestamp.lt = time.gmtime(timestamp.t)
        timestamp.YMDhms = tuple(timestamp.lt)[:6]
        canvas._doc._timeStamp = timestamp  # type: ignore[attr-defined]
        return canvas

    return factory


def extract_pdf_report_model(content: bytes) -> JsonMapping:
    """Extract the canonical model appended as a non-rendered PDF comment."""
    marker_index = content.rfind(PDF_MODEL_MARKER)
    if marker_index < 0:
        raise ValueError("PDF report model marker is missing")
    encoded = content[marker_index + len(PDF_MODEL_MARKER) :].splitlines()[0]
    try:
        raw: JsonValue = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("PDF report model marker is invalid") from exc
    if not isinstance(raw, dict):
        raise ValueError("PDF report model root must be an object")
    return raw


def _management_pages(
    document: ReportDocument,
    styles: _ReportStyles,
    width: float,
) -> list[Flowable]:
    periods = observed_periods(document)
    suppliers = observed_supplier_ids(document)
    overview = supplier_overview_rows(document)
    story: list[Flowable] = [
        Spacer(1, 8),
        Paragraph(escape(document.title), styles.title),
        Paragraph(
            escape(f"Q{document.scope.quarter} {document.scope.year} Management Report"),
            styles.subtitle,
        ),
        _cover_metadata(document, suppliers, styles, width),
        Spacer(1, 10),
        Paragraph("Executive Summary", styles.heading),
        _kpi_cards(document, suppliers, periods, styles, width),
        Spacer(1, 8),
        Paragraph(escape(document.executive_summary), styles.body),
        Paragraph("Key observations", styles.subheading),
        *_executive_observations(document, styles),
        Paragraph("Recommended focus", styles.subheading),
        *_recommendation_blocks(document, styles.compact, abbreviate_ids=True),
        PageBreak(),
        Paragraph("Supplier Quality Overview", styles.heading),
        Paragraph(
            "Monthly values reproduce existing defect-rate Calculation Evidence. "
            "Latest change is the final available month-over-month ratio delta.",
            styles.body,
        ),
        _supplier_overview_table(document, styles.table_body, width),
        Spacer(1, 8),
        Paragraph("Supplier Defect Rate - Q2 Monthly View", styles.subheading),
    ]
    chart = supplier_defect_rate_chart(overview, periods, width=width)
    if chart is None:
        story.append(Paragraph("No plottable defect-rate values are available.", styles.body))
    else:
        story.extend(
            [
                chart,
                Paragraph(
                    "Unit: percent. Each dot is a formatted view of one existing defect_rate "
                    "value; no ranking or aggregate KPI is calculated by the renderer.",
                    styles.caption,
                ),
            ]
        )
    story.extend(
        [
            PageBreak(),
            Paragraph("Applicable Quality Policies", styles.heading),
            Paragraph(
                "Controlled document references are summarized here. Full retrieved excerpts "
                "and Evidence identifiers are retained in Appendix B.",
                styles.body,
            ),
            _policy_summary_table(document, styles.table_body, width),
            Spacer(1, 8),
            *_policy_summary_blocks(document, styles),
            Paragraph("Data Coverage", styles.subheading),
            _data_table(document, styles.table_body, width, abbreviate_ids=True),
            Paragraph("Policy comparison boundary", styles.subheading),
            Paragraph(
                "The current contract retrieves controlled policy context but does not define "
                "a deterministic threshold-classification rule. This report therefore does not "
                "label suppliers as compliant, non-compliant, high risk, or low risk.",
                styles.body,
            ),
            PageBreak(),
            Paragraph("Findings and Recommended Actions", styles.heading),
            Paragraph("Major Findings", styles.subheading),
            _findings_table(document, styles.table_body, width),
            Spacer(1, 8),
            Paragraph("Business Risk Context", styles.subheading),
            _risk_table(document, styles.table_body, width),
            Spacer(1, 8),
            Paragraph("Recommended Actions", styles.subheading),
            _recommendations_table(document, styles.table_body, width),
            Paragraph(
                "No priority level is assigned because the current deterministic contract does "
                "not define an action-prioritization rule.",
                styles.caption,
            ),
            PageBreak(),
            Paragraph("Methodology and Limitations", styles.heading),
            Paragraph("Calculation methodology", styles.subheading),
            _formula_table(document, styles.table_body, width),
            Paragraph(
                "Ratios are displayed as percentages. Ratio deltas are displayed as percentage "
                "points (pp). Canonical raw ratios, operands, and units remain unchanged in "
                "Appendix A and in the embedded report model.",
                styles.body,
            ),
            Paragraph("Analytics notes and limitations", styles.subheading),
            _limitations_table(document, styles.table_body, width),
            Spacer(1, 8),
            Paragraph("Verification lifecycle", styles.subheading),
            Paragraph(
                "This PDF is created before the independent workflow verifier runs. The final "
                "verification result is stored with task and Artifact metadata; it is not "
                "hard-coded into this render-time document.",
                styles.body,
            ),
        ]
    )
    return story


def _appendices(
    document: ReportDocument,
    styles: _ReportStyles,
    width: float,
) -> list[Flowable]:
    return [
        PageBreak(),
        Paragraph("Appendix A - Detailed Calculation Metrics", styles.appendix_title),
        Paragraph(
            "All canonical Analytics observations are retained below for audit. Values and "
            "formula operands are raw; no presentation scaling is applied.",
            styles.body,
        ),
        _detailed_metrics_table(document, styles.table_body, width),
        PageBreak(),
        Paragraph("Appendix B - Evidence and Lineage", styles.appendix_title),
        Paragraph("Controlled document excerpts", styles.subheading),
        *_policy_appendix_blocks(document, styles),
        CondPageBreak(55 * mm),
        Paragraph("Database Evidence", styles.subheading),
        _data_table(document, styles.table_body, width, abbreviate_ids=False),
        CondPageBreak(55 * mm),
        Paragraph("Evidence lineage index", styles.subheading),
        _evidence_table(document, styles.table_body, width),
        PageBreak(),
        Paragraph("Appendix C - Execution Trace", styles.appendix_title),
        Paragraph(
            "Complete identifiers are wrapped for display only. The canonical ReportDocument "
            "and Artifact metadata preserve the exact values.",
            styles.body,
        ),
        _trace_table(document, styles.table_body, width),
        Spacer(1, 10),
        Paragraph("Render and execution metadata", styles.subheading),
        _render_metadata_table(document, styles.table_body, width),
    ]


def _register_report_font(language: ReportLanguage) -> str:
    """Use an embedded font for English and a standard CID font for Chinese."""
    if language is ReportLanguage.EN_US:
        font_name = "CopilotVera"
        if font_name not in pdfmetrics.getRegisteredFontNames():
            reportlab_root = Path(reportlab.__file__).resolve().parent
            pdfmetrics.registerFont(TTFont(font_name, reportlab_root / "fonts" / "Vera.ttf"))
        return font_name
    font_name = "STSong-Light"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(font_name))
    return font_name


def _build_styles(font_name: str) -> _ReportStyles:
    sample = getSampleStyleSheet()
    body = ParagraphStyle(
        "ReportBody",
        parent=sample["BodyText"],
        fontName=font_name,
        fontSize=9,
        leading=13,
        textColor=_TEXT,
        spaceAfter=5,
        splitLongWords=True,
    )
    compact = ParagraphStyle(
        "ReportCompact",
        parent=body,
        fontSize=8.2,
        leading=11,
        spaceAfter=3,
    )
    return _ReportStyles(
        title=ParagraphStyle(
            "ReportTitle",
            parent=sample["Title"],
            fontName=font_name,
            fontSize=24,
            leading=29,
            alignment=TA_CENTER,
            textColor=_DARK_BLUE,
            spaceAfter=4,
        ),
        subtitle=ParagraphStyle(
            "ReportSubtitle",
            parent=body,
            fontSize=12,
            leading=16,
            alignment=TA_CENTER,
            textColor=_MID_BLUE,
            spaceAfter=14,
        ),
        heading=ParagraphStyle(
            "ReportHeading",
            parent=sample["Heading2"],
            fontName=font_name,
            fontSize=16,
            leading=20,
            textColor=_DARK_BLUE,
            spaceBefore=4,
            spaceAfter=8,
            keepWithNext=True,
        ),
        subheading=ParagraphStyle(
            "ReportSubheading",
            parent=sample["Heading3"],
            fontName=font_name,
            fontSize=11,
            leading=14,
            textColor=_MID_BLUE,
            spaceBefore=8,
            spaceAfter=5,
            keepWithNext=True,
        ),
        body=body,
        compact=compact,
        caption=ParagraphStyle(
            "ReportCaption",
            parent=compact,
            fontSize=7.2,
            leading=9.5,
            textColor=_MUTED,
        ),
        table_body=ParagraphStyle(
            "ReportTableBody",
            parent=compact,
            fontSize=7.6,
            leading=9.5,
            spaceAfter=0,
        ),
        appendix_title=ParagraphStyle(
            "ReportAppendixTitle",
            parent=sample["Heading1"],
            fontName=font_name,
            fontSize=17,
            leading=21,
            textColor=_DARK_BLUE,
            spaceAfter=8,
            keepWithNext=True,
        ),
    )


def _draw_page_header_footer(page_canvas: Canvas, doc: BaseDocTemplate) -> None:
    page_canvas.saveState()
    page_canvas.setFont("Helvetica", 7.5)
    page_canvas.setFillColor(_MUTED)
    page_canvas.drawString(16 * mm, A4[1] - 10 * mm, "Supplier Quality Analysis")
    page_canvas.drawString(
        16 * mm,
        9 * mm,
        "Internal report - final verification status is recorded with task metadata",
    )
    page_canvas.drawRightString(A4[0] - 16 * mm, 9 * mm, f"Page {doc.page}")
    page_canvas.restoreState()


def _paragraph(value: object, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(value)), style)


def _styled_table(
    rows: list[list[object]],
    widths: list[float],
    body: ParagraphStyle,
    *,
    numeric_columns: tuple[int, ...] = (),
) -> Table:
    header = ParagraphStyle("TableHeader", parent=body, textColor=colors.white)
    normalized = [
        [
            cell
            if isinstance(cell, Flowable)
            else _paragraph(cell, header if row_index == 0 else body)
            for cell in row
        ]
        for row_index, row in enumerate(rows)
    ]
    table = Table(
        normalized,
        colWidths=widths,
        repeatRows=1,
        hAlign="LEFT",
        splitByRow=1,
    )
    commands: list[Any] = [
        ("BACKGROUND", (0, 0), (-1, 0), _DARK_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.3, _BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _LIGHT]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    commands.extend(("ALIGN", (column, 1), (column, -1), "RIGHT") for column in numeric_columns)
    table.setStyle(TableStyle(commands))
    return table


def _cover_metadata(
    document: ReportDocument,
    suppliers: tuple[str, ...],
    styles: _ReportStyles,
    width: float,
) -> Table:
    scope = document.scope
    supplier_text = (
        f"{len(suppliers)} represented"
        if suppliers
        else (f"{len(scope.supplier_ids)} authorized" if scope.supplier_ids else "No records")
    )
    if not scope.supplier_ids and suppliers:
        supplier_text += " (resolved tenant-wide scope)"
    rows = [
        ["Period", f"{scope.start_date.isoformat()} to {scope.end_date.isoformat()}"],
        ["Supplier scope", supplier_text],
        ["Report generated", document.execution_metadata.generated_at.isoformat()],
        ["Task", short_identifier(document.task_summary.task_id)],
    ]
    label_style = ParagraphStyle("CoverLabel", parent=styles.compact, textColor=colors.white)
    table = Table(
        [
            [_paragraph(label, label_style), _paragraph(value, styles.compact)]
            for label, value in rows
        ],
        colWidths=[34 * mm, width - 34 * mm],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), _DARK_BLUE),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
                ("BACKGROUND", (1, 0), (1, -1), _LIGHT),
                ("GRID", (0, 0), (-1, -1), 0.35, _BORDER),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _kpi_cards(
    document: ReportDocument,
    suppliers: tuple[str, ...],
    periods: tuple[str, ...],
    styles: _ReportStyles,
    width: float,
) -> Table:
    scope = document.scope
    cards = (
        ("Analysis period", f"Q{scope.quarter} {scope.year}"),
        ("Suppliers represented", str(len(suppliers)) if suppliers else "No records"),
        ("Observed months", str(len(periods)) if periods else "None"),
        ("Evidence items", str(len(document.evidence))),
    )
    card_cells = [
        Paragraph(
            f"<font color='#607D8B' size='7'>{escape(label)}</font><br/>"
            f"<font color='#123B5D' size='14'><b>{escape(value)}</b></font>",
            styles.compact,
        )
        for label, value in cards
    ]
    table = Table([card_cells[:2], card_cells[2:]], colWidths=[width / 2, width / 2])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.4, _BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.white),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def _executive_observations(
    document: ReportDocument,
    styles: _ReportStyles,
) -> list[Flowable]:
    if not document.major_findings:
        return [Paragraph("No deterministic finding was produced.", styles.compact)]
    return [
        Paragraph(
            f"<b>{escape(item.title)}.</b> {escape(item.statement)}",
            styles.compact,
            bulletText="-",
        )
        for item in document.major_findings[:5]
    ]


def _supplier_overview_table(
    document: ReportDocument,
    body: ParagraphStyle,
    width: float,
) -> Table:
    periods = observed_periods(document)
    rows: list[list[object]] = [
        ["Supplier", *(f"{period_label(period)} rate" for period in periods), "Latest change"]
    ]
    for row in supplier_overview_rows(document):
        rows.append(
            [
                row.supplier_id,
                *(format_metric_value(value, "ratio") for value in row.defect_rates),
                format_metric_value(row.latest_month_change, "ratio_delta"),
            ]
        )
    if len(rows) == 1:
        rows.append(["No supplier data", *("N/A" for _ in periods), "N/A"])
    supplier_width = 32 * mm
    value_width = (width - supplier_width) / max(1, len(periods) + 1)
    return _styled_table(
        rows,
        [supplier_width, *([value_width] * (len(periods) + 1))],
        body,
        numeric_columns=tuple(range(1, len(periods) + 2)),
    )


def _policy_summary_table(
    document: ReportDocument,
    body: ParagraphStyle,
    width: float,
) -> Table:
    rows: list[list[object]] = [
        ["Document", "Version", "Relevant topic", "Page / chunk", "Evidence"]
    ]
    rows.extend(
        [
            item.document_id,
            item.document_version or "Not recorded",
            "Retrieved quality-policy context",
            item.location,
            short_identifier(item.evidence_id),
        ]
        for item in document.applicable_policies
    )
    if len(rows) == 1:
        rows.append(["No controlled policy Evidence", "-", "-", "-", "-"])
    return _styled_table(rows, [33 * mm, 22 * mm, 52 * mm, 27 * mm, width - 134 * mm], body)


def _policy_summary_blocks(
    document: ReportDocument,
    styles: _ReportStyles,
) -> list[Flowable]:
    blocks: list[Flowable] = []
    for item in document.applicable_policies:
        excerpt = item.excerpt.strip()
        if len(excerpt) > 260:
            excerpt = excerpt[:257].rstrip() + "..."
        blocks.append(
            KeepTogether(
                [
                    Paragraph(escape(item.document_id), styles.subheading),
                    Paragraph(escape(excerpt), styles.compact),
                ]
            )
        )
    return blocks


def _policy_appendix_blocks(
    document: ReportDocument,
    styles: _ReportStyles,
) -> list[Flowable]:
    if not document.applicable_policies:
        return [Paragraph("No controlled policy Evidence was available.", styles.body)]
    return [
        KeepTogether(
            [
                Paragraph(
                    f"<b>{escape(item.document_id)}</b> - version "
                    f"{escape(item.document_version or 'not recorded')}, "
                    f"location {escape(item.location)}",
                    styles.compact,
                ),
                Paragraph(escape(item.excerpt), styles.compact),
                Paragraph(
                    f"Evidence ID: {escape(wrap_identifier(item.evidence_id))}",
                    styles.caption,
                ),
                Spacer(1, 4),
            ]
        )
        for item in document.applicable_policies
    ]


def _data_table(
    document: ReportDocument,
    body: ParagraphStyle,
    width: float,
    *,
    abbreviate_ids: bool,
) -> Table:
    rows: list[list[object]] = [["Evidence", "Query ID", "Rows", "Snapshot"]]
    for item in document.data_overview:
        evidence_id = (
            short_identifier(item.evidence_id)
            if abbreviate_ids
            else wrap_identifier(item.evidence_id)
        )
        query_id = (
            short_identifier(item.query_id) if abbreviate_ids else wrap_identifier(item.query_id)
        )
        rows.append([evidence_id, query_id, item.row_count, item.snapshot_at or "Not recorded"])
    if len(rows) == 1:
        rows.append(["No database Evidence", "-", 0, "-"])
    return _styled_table(
        rows,
        [36 * mm, 61 * mm, 18 * mm, width - 115 * mm],
        body,
        numeric_columns=(2,),
    )


def _findings_table(document: ReportDocument, body: ParagraphStyle, width: float) -> Table:
    rows: list[list[object]] = [["Finding", "Evidence-backed observation", "Evidence"]]
    rows.extend(
        [
            item.title,
            item.statement,
            ", ".join(short_identifier(value) for value in item.evidence_ids),
        ]
        for item in document.major_findings[:8]
    )
    if len(rows) == 1:
        rows.append(["No finding", "No deterministic finding was produced.", "-"])
    return _styled_table(rows, [39 * mm, 92 * mm, width - 131 * mm], body)


def _risk_table(document: ReportDocument, body: ParagraphStyle, width: float) -> Table:
    rows: list[list[object]] = [["Classification", "Statement", "Evidence"]]
    rows.extend(
        [
            item.level,
            item.statement,
            ", ".join(short_identifier(value) for value in item.evidence_ids),
        ]
        for item in document.risk_analysis
    )
    return _styled_table(rows, [35 * mm, 101 * mm, width - 136 * mm], body)


def _recommendations_table(
    document: ReportDocument,
    body: ParagraphStyle,
    width: float,
) -> Table:
    rows: list[list[object]] = [["Action", "Basis", "Evidence"]]
    rows.extend(
        [
            item.action,
            item.basis,
            ", ".join(short_identifier(value) for value in item.evidence_ids) or "-",
        ]
        for item in document.recommended_actions
    )
    return _styled_table(rows, [91 * mm, 35 * mm, width - 126 * mm], body)


def _recommendation_blocks(
    document: ReportDocument,
    body: ParagraphStyle,
    *,
    abbreviate_ids: bool,
) -> list[Flowable]:
    blocks: list[Flowable] = []
    for item in document.recommended_actions:
        ids = (
            ", ".join(short_identifier(value) for value in item.evidence_ids)
            if abbreviate_ids
            else ", ".join(item.evidence_ids)
        )
        suffix = f" Evidence: {ids}." if ids else ""
        blocks.append(Paragraph(escape(f"{item.action}{suffix}"), body, bulletText="-"))
    return blocks


def _formula_table(document: ReportDocument, body: ParagraphStyle, width: float) -> Table:
    formulas: dict[str, str] = {}
    for item in document.evidence:
        formulas.update(item.formulas)
    rows: list[list[object]] = [["Metric", "Controlled formula"]]
    rows.extend([metric, formula] for metric, formula in sorted(formulas.items()))
    if len(rows) == 1:
        rows.append(["No formula metadata", "Not available"])
    return _styled_table(rows, [50 * mm, width - 50 * mm], body)


def _limitations_table(document: ReportDocument, body: ParagraphStyle, width: float) -> Table:
    rows: list[list[object]] = [["Code", "Interpretation boundary"]]
    rows.extend([item.code, item.statement] for item in document.limitations)
    return _styled_table(rows, [52 * mm, width - 52 * mm], body)


def _detailed_metrics_table(
    document: ReportDocument,
    body: ParagraphStyle,
    width: float,
) -> Table:
    rows: list[list[object]] = [
        ["Metric", "Supplier", "Period", "Raw value", "Unit", "Formula operands"]
    ]
    for item in document.key_metrics:
        rows.append(
            [
                item.metric.value,
                item.dimensions.get("supplier_id", "-"),
                item.dimensions.get("period", "-"),
                raw_metric_value(item.value),
                item.unit,
                f"{raw_metric_value(item.numerator)} / {raw_metric_value(item.denominator)}",
            ]
        )
    if len(rows) == 1:
        rows.append(["No metrics", "-", "-", "-", "-", "-"])
    return _styled_table(
        rows,
        [39 * mm, 26 * mm, 24 * mm, 22 * mm, 23 * mm, width - 134 * mm],
        body,
        numeric_columns=(3,),
    )


def _evidence_table(document: ReportDocument, body: ParagraphStyle, width: float) -> Table:
    rows: list[list[object]] = [
        ["Evidence ID", "Type", "Query / formula", "Input lineage", "Checksum"]
    ]
    for item in document.evidence:
        query_or_formula = item.query_id or "; ".join(
            f"{key}: {value}" for key, value in sorted(item.formulas.items())
        )
        rows.append(
            [
                wrap_identifier(item.evidence_id),
                item.source_type.value,
                wrap_identifier(query_or_formula, width=28) if query_or_formula else "-",
                ", ".join(wrap_identifier(value) for value in item.input_evidence_ids) or "-",
                wrap_identifier(item.checksum),
            ]
        )
    return _styled_table(
        rows,
        [34 * mm, 25 * mm, 52 * mm, 31 * mm, width - 142 * mm],
        body,
    )


def _trace_table(document: ReportDocument, body: ParagraphStyle, width: float) -> Table:
    rows: list[list[object]] = [["Step", "Tool Call", "Evidence", "Type"]]
    rows.extend(
        [
            wrap_identifier(item.step_id, width=22),
            wrap_identifier(item.tool_call_id),
            wrap_identifier(item.evidence_id),
            item.source_type.value,
        ]
        for item in document.execution_trace
    )
    return _styled_table(rows, [57 * mm, 43 * mm, 43 * mm, width - 143 * mm], body)


def _render_metadata_table(
    document: ReportDocument,
    body: ParagraphStyle,
    width: float,
) -> Table:
    metadata = document.execution_metadata
    rows: list[list[object]] = [
        ["Field", "Value"],
        ["Task ID", wrap_identifier(document.task_summary.task_id)],
        [
            "Workflow trace",
            "Stored with Task metadata; report_generator.v1 input carries Task ID only",
        ],
        ["Generated at", metadata.generated_at.isoformat()],
        ["Schema version", metadata.schema_version],
        ["Template version", metadata.template_version],
        ["Generator version", metadata.generator_version],
        [
            "Verification at render time",
            "Pending - the independent verifier runs after Artifact creation",
        ],
    ]
    return _styled_table(rows, [50 * mm, width - 50 * mm], body)


__all__ = [
    "JsonReportRenderer",
    "PDF_MODEL_MARKER",
    "PdfReportRenderer",
    "RenderedReport",
    "RendererRegistry",
    "canonical_report_json",
    "extract_pdf_report_model",
]
