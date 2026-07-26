"""Deterministic renderers for the two frozen v1.0 report formats."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Protocol, cast
from xml.sax.saxutils import escape

import reportlab
from pydantic import JsonValue
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
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
from copilot.tools.reporting.exceptions import (
    ReportRenderingError,
    UnsupportedReportFormatError,
)
from copilot.tools.reporting.schemas import ReportDocument, ReportFormat

PDF_MODEL_MARKER = b"%COPILOT_REPORT_MODEL:"


@dataclass(frozen=True, slots=True)
class RenderedReport:
    """Validated renderer output before governed persistence."""

    content: bytes
    media_type: str
    extension: str


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
    """Polished offline PDF renderer backed by the common ReportDocument."""

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
        buffer = BytesIO()
        styles = getSampleStyleSheet()
        body = ParagraphStyle(
            "ReportBody",
            parent=styles["BodyText"],
            fontName=font_name,
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#263238"),
            spaceAfter=5,
        )
        heading = ParagraphStyle(
            "ReportHeading",
            parent=styles["Heading2"],
            fontName=font_name,
            fontSize=14,
            leading=18,
            textColor=colors.HexColor("#123B5D"),
            spaceBefore=10,
            spaceAfter=6,
        )
        title = ParagraphStyle(
            "ReportTitle",
            parent=heading,
            fontSize=21,
            leading=25,
            alignment=TA_CENTER,
            spaceAfter=12,
        )
        doc = BaseDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=18 * mm,
            bottomMargin=17 * mm,
            title=document.title,
            author="Agentic Enterprise Knowledge Copilot",
            subject="Governed Supplier Quality Analysis",
        )
        frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
        doc.addPageTemplates(
            PageTemplate(id="report", frames=[frame], onPage=_draw_page_header_footer)
        )
        story: list[Flowable] = [
            Paragraph(escape(document.title), title),
            _metadata_table(document, body),
            Spacer(1, 8),
            Paragraph("Executive Summary", heading),
            Paragraph(escape(document.executive_summary), body),
            Paragraph("Scope", heading),
            _scope_table(document, body),
            Paragraph("Applicable Policies", heading),
            *_policy_blocks(document, body),
            Paragraph("Data Overview", heading),
            _data_table(document, body),
            PageBreak(),
            Paragraph("Key Metrics", heading),
            _metrics_table(document, body),
            Paragraph("Supplier Ranking", heading),
            Paragraph(
                "Not produced by the frozen quality_metrics.v1 analytics contract.",
                body,
            ),
            Paragraph("Major Findings", heading),
            *_finding_blocks(document, body),
            Paragraph("Risk Analysis", heading),
            *_risk_blocks(document, body),
            KeepTogether(
                [
                    Paragraph("Recommended Actions", heading),
                    *_recommendation_blocks(document, body),
                ]
            ),
            KeepTogether(
                [
                    Paragraph("Limitations", heading),
                    *_limitation_blocks(document, body),
                ]
            ),
            CondPageBreak(45 * mm),
            Paragraph("Evidence and Sources", heading),
            _evidence_table(document, body),
            CondPageBreak(45 * mm),
            Paragraph("Execution Trace", heading),
            _trace_table(document, body),
        ]
        doc.build(story)
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


def _draw_page_header_footer(page_canvas: Canvas, doc: BaseDocTemplate) -> None:
    page_canvas.saveState()
    page_canvas.setFont("Helvetica", 8)
    page_canvas.setFillColor(colors.HexColor("#607D8B"))
    page_canvas.drawString(18 * mm, A4[1] - 10 * mm, "Supplier Quality Analysis")
    page_canvas.drawRightString(
        A4[0] - 18 * mm,
        10 * mm,
        f"Page {doc.page}",
    )
    page_canvas.restoreState()


def _paragraph(value: object, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(value)), style)


def _styled_table(
    rows: list[list[object]],
    widths: list[float],
    body: ParagraphStyle,
) -> Table:
    header = ParagraphStyle("TableHeader", parent=body, textColor=colors.white)
    normalized = [
        [_paragraph(cell, header if row_index == 0 else body) for cell in row]
        for row_index, row in enumerate(rows)
    ]
    table = Table(normalized, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#123B5D")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B0BEC5")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F8FA")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _metadata_table(document: ReportDocument, body: ParagraphStyle) -> Table:
    meta = document.execution_metadata
    scope = document.scope
    return _styled_table(
        [
            ["Task ID", document.task_summary.task_id, "Trace ID", document.task_summary.trace_id],
            [
                "Generated",
                meta.generated_at.isoformat(),
                "Verification",
                meta.verification_status,
            ],
            [
                "Period",
                f"{scope.start_date.isoformat()} to {scope.end_date.isoformat()}",
                "Schema",
                meta.schema_version,
            ],
        ],
        [26 * mm, 56 * mm, 27 * mm, 58 * mm],
        body,
    )


def _scope_table(document: ReportDocument, body: ParagraphStyle) -> Table:
    scope = document.scope
    return _styled_table(
        [
            ["Year", "Quarter", "Start", "End", "Suppliers"],
            [
                scope.year,
                scope.quarter,
                scope.start_date,
                scope.end_date,
                ", ".join(scope.supplier_ids) or "Authorized resolved scope",
            ],
        ],
        [18 * mm, 18 * mm, 31 * mm, 31 * mm, 69 * mm],
        body,
    )


def _policy_blocks(document: ReportDocument, body: ParagraphStyle) -> list[Flowable]:
    if not document.applicable_policies:
        return [Paragraph("No controlled policy Evidence was available.", body)]
    return [
        KeepTogether(
            [
                Paragraph(
                    f"<b>{escape(item.document_id)}</b> "
                    f"({escape(item.document_version or 'version unavailable')}, "
                    f"{escape(item.location)})",
                    body,
                ),
                Paragraph(escape(item.excerpt), body),
                Paragraph(f"Evidence: {escape(item.evidence_id)}", body),
            ]
        )
        for item in document.applicable_policies
    ]


def _data_table(document: ReportDocument, body: ParagraphStyle) -> Table:
    rows: list[list[object]] = [["Evidence", "Query ID", "Rows", "Snapshot"]]
    rows.extend(
        [
            item.evidence_id,
            item.query_id,
            item.row_count,
            item.snapshot_at or "not recorded",
        ]
        for item in document.data_overview
    )
    return _styled_table(rows, [33 * mm, 67 * mm, 18 * mm, 49 * mm], body)


def _metrics_table(document: ReportDocument, body: ParagraphStyle) -> Table:
    rows: list[list[object]] = [["Metric", "Dimensions", "Value", "Unit", "Formula operands"]]
    for item in document.key_metrics:
        dimensions = ", ".join(f"{key}={value}" for key, value in sorted(item.dimensions.items()))
        rows.append(
            [
                item.metric.value,
                dimensions or "-",
                "null" if item.value is None else item.value,
                item.unit,
                f"{item.numerator!s} / {item.denominator!s}",
            ]
        )
    if len(rows) == 1:
        rows.append(["No metrics", "-", "-", "-", "-"])
    return _styled_table(rows, [34 * mm, 47 * mm, 24 * mm, 23 * mm, 39 * mm], body)


def _finding_blocks(document: ReportDocument, body: ParagraphStyle) -> list[Flowable]:
    if not document.major_findings:
        return [Paragraph("No deterministic finding was produced.", body)]
    return [
        Paragraph(
            f"<b>{escape(item.title)}</b>: {escape(item.statement)} "
            f"[{escape(', '.join(item.evidence_ids))}]",
            body,
        )
        for item in document.major_findings
    ]


def _risk_blocks(document: ReportDocument, body: ParagraphStyle) -> list[Flowable]:
    return [
        Paragraph(
            f"<b>{escape(item.level)}</b>: {escape(item.statement)} "
            f"[{escape(', '.join(item.evidence_ids))}]",
            body,
        )
        for item in document.risk_analysis
    ]


def _recommendation_blocks(document: ReportDocument, body: ParagraphStyle) -> list[Flowable]:
    return [
        Paragraph(
            f"{escape(item.action)} ({escape(item.basis)})"
            + (f" [{escape(', '.join(item.evidence_ids))}]" if item.evidence_ids else ""),
            body,
        )
        for item in document.recommended_actions
    ]


def _limitation_blocks(document: ReportDocument, body: ParagraphStyle) -> list[Flowable]:
    return [
        Paragraph(f"<b>{escape(item.code)}</b>: {escape(item.statement)}", body)
        for item in document.limitations
    ]


def _evidence_table(document: ReportDocument, body: ParagraphStyle) -> Table:
    rows: list[list[object]] = [["Evidence ID", "Type", "Query / Formula", "Lineage"]]
    for item in document.evidence:
        query_or_formula = item.query_id or "; ".join(
            f"{key}: {value}" for key, value in sorted(item.formulas.items())
        )
        rows.append(
            [
                item.evidence_id,
                item.source_type.value,
                query_or_formula or "-",
                ", ".join(item.input_evidence_ids) or "-",
            ]
        )
    return _styled_table(rows, [35 * mm, 30 * mm, 67 * mm, 35 * mm], body)


def _trace_table(document: ReportDocument, body: ParagraphStyle) -> Table:
    rows: list[list[object]] = [["Step", "Tool Call", "Evidence", "Type"]]
    rows.extend(
        [item.step_id, item.tool_call_id, item.evidence_id, item.source_type.value]
        for item in document.execution_trace
    )
    return _styled_table(rows, [48 * mm, 43 * mm, 43 * mm, 33 * mm], body)


__all__ = [
    "JsonReportRenderer",
    "PDF_MODEL_MARKER",
    "PdfReportRenderer",
    "RenderedReport",
    "RendererRegistry",
    "canonical_report_json",
    "extract_pdf_report_model",
]
