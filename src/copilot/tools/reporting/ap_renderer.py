"""Deterministic JSON/PDF renderers for the Accounts Payable report profile."""

from __future__ import annotations

import base64
import json
from io import BytesIO
from typing import Protocol, cast
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from copilot.contracts.base import JsonMapping
from copilot.tools.reporting.ap_schemas import AccountsPayableReportV1
from copilot.tools.reporting.exceptions import (
    ReportRenderingError,
    UnsupportedReportFormatError,
)
from copilot.tools.reporting.renderer import (
    PDF_MODEL_MARKER,
    RenderedReport,
    _canvas_factory,
    _register_report_font,
)
from copilot.tools.reporting.schemas import ReportFormat

_DARK = colors.HexColor("#17324D")
_MID = colors.HexColor("#2C6E8F")
_LIGHT = colors.HexColor("#F3F6F8")
_BORDER = colors.HexColor("#B8C5CC")


class APReportRenderer(Protocol):
    """Pure renderer interface for one canonical AP report model."""

    format: ReportFormat

    def render(self, document: AccountsPayableReportV1) -> RenderedReport:
        """Render bytes without persistence or external publication."""
        ...


def canonical_ap_report_json(document: AccountsPayableReportV1, *, pretty: bool) -> bytes:
    """Serialize stable finite JSON shared by the AP JSON and PDF profiles."""
    payload = cast(JsonMapping, document.model_dump(mode="json"))
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class APJsonReportRenderer:
    """Stable AP JSON renderer."""

    format = ReportFormat.JSON

    def render(self, document: AccountsPayableReportV1) -> RenderedReport:
        try:
            content = canonical_ap_report_json(document, pretty=True)
        except (TypeError, ValueError) as exc:
            raise ReportRenderingError() from exc
        return RenderedReport(content=content, media_type="application/json", extension=".json")


class APPdfReportRenderer:
    """Management PDF carrying the exact canonical AP model for independent verification."""

    format = ReportFormat.PDF

    def render(self, document: AccountsPayableReportV1) -> RenderedReport:
        try:
            content = self._render_pdf(document)
            canonical = base64.b64encode(canonical_ap_report_json(document, pretty=False))
            content += b"\n" + PDF_MODEL_MARKER + canonical + b"\n"
        except Exception as exc:
            raise ReportRenderingError() from exc
        return RenderedReport(content=content, media_type="application/pdf", extension=".pdf")

    @staticmethod
    def _render_pdf(document: AccountsPayableReportV1) -> bytes:
        font = _register_report_font(document.execution_metadata.language)
        styles = getSampleStyleSheet()
        title = ParagraphStyle(
            "APTitle",
            parent=styles["Title"],
            fontName=font,
            fontSize=22,
            leading=27,
            alignment=TA_CENTER,
            textColor=_DARK,
            spaceAfter=12,
        )
        heading = ParagraphStyle(
            "APHeading",
            parent=styles["Heading2"],
            fontName=font,
            fontSize=15,
            leading=19,
            textColor=_DARK,
            spaceBefore=6,
            spaceAfter=7,
        )
        body = ParagraphStyle(
            "APBody",
            parent=styles["BodyText"],
            fontName=font,
            fontSize=8.5,
            leading=12,
            textColor=colors.HexColor("#263238"),
        )
        buffer = BytesIO()
        pdf = BaseDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=16 * mm,
            rightMargin=16 * mm,
            topMargin=16 * mm,
            bottomMargin=16 * mm,
            title=document.title,
            author="Agentic Enterprise Knowledge Copilot",
            subject="Governed Accounts Payable Exception Analysis",
            invariant=True,
            pageCompression=0,
        )
        frame = Frame(pdf.leftMargin, pdf.bottomMargin, pdf.width, pdf.height, id="ap-report")
        pdf.addPageTemplates(PageTemplate(id="ap", frames=[frame]))
        metrics = document.exception_summary.metrics.root
        metric_rows: list[list[object]] = [["Metric", "Canonical value"]]
        metric_rows.extend(
            [key, _display(value)]
            for key, value in sorted(metrics.items())
            if not isinstance(value, dict)
        )
        policy_rows: list[list[object]] = [["Document", "Version", "Rule IDs", "Evidence"]]
        policy_rows.extend(
            [
                item.document_id,
                item.document_version,
                ", ".join(item.rule_ids),
                item.evidence_id,
            ]
            for item in document.applicable_policies
        )
        story = [
            Spacer(1, 8),
            Paragraph(escape(document.title), title),
            _table(
                [
                    ["Scope", "Value"],
                    [
                        "Invoice dates",
                        f"{document.scope.start_date.isoformat()} to "
                        f"{document.scope.end_date.isoformat()}",
                    ],
                    ["Legal entities", str(len(document.scope.legal_entity_ids))],
                    ["Suppliers", str(len(document.scope.supplier_ids)) or "Authorized scope"],
                    ["Detail mode", document.execution_metadata.detail_access.value],
                ],
                body,
                pdf.width,
            ),
            Spacer(1, 9),
            Paragraph("Executive Summary", heading),
            Paragraph(escape(document.executive_summary), body),
            Paragraph("Exception Summary", heading),
            _table(metric_rows, body, pdf.width),
            Paragraph("Amounts by Currency", heading),
            _table(_currency_rows(metrics), body, pdf.width),
            PageBreak(),
            Paragraph("Applicable Policies", heading),
            _table(policy_rows, body, pdf.width),
            Paragraph("Supplier Summary", heading),
            _table(
                [
                    ["Supplier", "Eligible", "Exceptions", "Rate", "Exclusions"],
                    *[
                        [
                            item.supplier_id,
                            item.eligible_invoice_count,
                            item.exception_invoice_count,
                            item.supplier_exception_rate or "N/A",
                            item.exclusion_count,
                        ]
                        for item in document.supplier_summary
                    ],
                ],
                body,
                pdf.width,
            ),
            Paragraph("Risk Observations", heading),
            *[
                Paragraph(escape(f"{item.level}: {item.statement}"), body, bulletText="-")
                for item in document.risk_observations
            ],
            Paragraph("Recommended Actions", heading),
            *[
                Paragraph(escape(item.action), body, bulletText="-")
                for item in document.recommended_actions
            ],
            PageBreak(),
            Paragraph("Management Detail", heading),
            _detail_table(document, body, pdf.width),
            Paragraph("Limitations", heading),
            *[
                Paragraph(escape(f"{item.code}: {item.statement}"), body, bulletText="-")
                for item in document.limitations
            ],
            Paragraph("Evidence and Execution Trace", heading),
            Paragraph(
                escape(
                    f"{len(document.evidence.references)} Evidence item(s), "
                    f"{len(document.evidence.claims)} structured claim(s), and "
                    f"{len(document.execution_trace)} execution trace entry/entries."
                ),
                body,
            ),
        ]
        pdf.build(
            story,
            canvasmaker=_canvas_factory(document.execution_metadata.generated_at),
        )
        return buffer.getvalue()


class APRendererRegistry:
    """Closed AP renderer registry."""

    def __init__(self, renderers: tuple[APReportRenderer, ...] | None = None) -> None:
        selected = renderers or (APJsonReportRenderer(), APPdfReportRenderer())
        self._renderers = {renderer.format: renderer for renderer in selected}

    def get(self, report_format: ReportFormat) -> APReportRenderer:
        try:
            return self._renderers[report_format]
        except KeyError as exc:
            raise UnsupportedReportFormatError() from exc


def _table(rows: list[list[object]], body: ParagraphStyle, width: float) -> Table:
    if len(rows) == 1:
        rows.append(["No governed records", *["-"] * (len(rows[0]) - 1)])
    header = ParagraphStyle("APTableHeader", parent=body, textColor=colors.white)
    normalized = [
        [Paragraph(escape(str(value)), header if row_index == 0 else body) for value in row]
        for row_index, row in enumerate(rows)
    ]
    columns = len(rows[0])
    table = Table(normalized, colWidths=[width / columns] * columns, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _DARK),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _LIGHT]),
                ("GRID", (0, 0), (-1, -1), 0.3, _BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _currency_rows(metrics: JsonMapping) -> list[list[object]]:
    rows: list[list[object]] = [["Metric", "Currency", "Canonical amount"]]
    for metric, value in sorted(metrics.items()):
        if not isinstance(value, dict):
            continue
        for currency, amount in sorted(value.items()):
            rows.append([metric, currency, _display(amount)])
    return rows


def _detail_table(document: AccountsPayableReportV1, body: ParagraphStyle, width: float) -> Table:
    details = (
        *document.duplicate_invoice_findings,
        *document.po_compliance_findings,
        *document.payment_findings,
    )
    rows: list[list[object]] = [["Type", "Supplier", "Currency", "Status", "Opaque invoice key"]]
    rows.extend(
        [
            item.exception_type,
            item.supplier_id,
            item.currency,
            item.status.value,
            item.invoice_record_key or "Aggregate only",
        ]
        for item in details
    )
    return _table(rows, body, width)


def _display(value: object) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


__all__ = [
    "APJsonReportRenderer",
    "APPdfReportRenderer",
    "APRendererRegistry",
    "canonical_ap_report_json",
]
