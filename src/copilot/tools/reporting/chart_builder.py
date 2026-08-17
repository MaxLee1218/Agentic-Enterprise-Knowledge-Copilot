"""Auditable ReportLab charts sourced only from existing Calculation Evidence values."""

from __future__ import annotations

import math

from reportlab.graphics.shapes import Circle, Drawing, Line, Rect, String
from reportlab.lib import colors

from copilot.tools.reporting.presentation import SupplierOverviewRow, period_label

_SERIES_COLORS = (
    colors.HexColor("#2F6B8A"),
    colors.HexColor("#D28B32"),
    colors.HexColor("#6E7781"),
)


def supplier_defect_rate_chart(
    rows: tuple[SupplierOverviewRow, ...],
    periods: tuple[str, ...],
    *,
    width: float,
) -> Drawing | None:
    """Create an alphanumeric supplier dot plot without ranking or metric recomputation."""
    available = [
        float(value) for row in rows for value in row.defect_rates if isinstance(value, int | float)
    ]
    if not rows or not periods or not available:
        return None

    height = 34 + len(rows) * 13
    drawing = Drawing(width, height)
    plot_left = 57.0
    plot_right = width - 12.0
    plot_width = plot_right - plot_left
    axis_percent = max(1, math.ceil(max(available) * 100))

    drawing.add(
        String(
            0,
            height - 9,
            "Supplier order is alphanumeric, not a risk ranking.",
            fontName="Helvetica",
            fontSize=7,
            fillColor=colors.HexColor("#52606D"),
        )
    )
    legend_x = max(plot_left, width - 118)
    for index, period in enumerate(periods[: len(_SERIES_COLORS)]):
        x = legend_x + index * 39
        drawing.add(Circle(x, height - 8, 2.2, fillColor=_SERIES_COLORS[index], strokeColor=None))
        drawing.add(
            String(
                x + 4,
                height - 10.5,
                period_label(period),
                fontName="Helvetica",
                fontSize=7,
                fillColor=colors.HexColor("#263238"),
            )
        )

    top = height - 25
    for tick in range(axis_percent + 1):
        x = plot_left + (tick / axis_percent) * plot_width
        drawing.add(
            Line(
                x,
                6,
                x,
                top + 3,
                strokeColor=colors.HexColor("#E3E8EC"),
                strokeWidth=0.4,
            )
        )
        drawing.add(
            String(
                x - 4,
                0,
                f"{tick}%",
                fontName="Helvetica",
                fontSize=6.5,
                fillColor=colors.HexColor("#52606D"),
            )
        )

    for row_index, row in enumerate(rows):
        y = top - row_index * 13
        drawing.add(
            String(
                0,
                y - 2.5,
                row.supplier_id,
                fontName="Helvetica",
                fontSize=7,
                fillColor=colors.HexColor("#263238"),
            )
        )
        if row_index % 2:
            background = Rect(plot_left, y - 5, plot_width, 10)
            background.fillColor = colors.HexColor("#F7F9FA")
            background.strokeColor = None
            drawing.add(background)
        for period_index, value in enumerate(row.defect_rates[: len(_SERIES_COLORS)]):
            if not isinstance(value, int | float):
                continue
            x = plot_left + ((float(value) * 100) / axis_percent) * plot_width
            drawing.add(
                Circle(
                    x,
                    y + (period_index - 1) * 2.5,
                    2.2,
                    fillColor=_SERIES_COLORS[period_index],
                    strokeColor=colors.white,
                    strokeWidth=0.35,
                )
            )
    return drawing


__all__ = ["supplier_defect_rate_chart"]
