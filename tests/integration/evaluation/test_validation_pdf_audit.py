"""Visible-PDF scope controls independent of product rendering and frozen V inputs."""

import base64
import json
from pathlib import Path

import pytest
from reportlab.pdfgen.canvas import Canvas

from evaluation.supplier_quality_validation.audit import pdf_scope_assertion


@pytest.mark.parametrize("scope,chart,passed", [(1, 2, False), (2, 2, True), (3, 2, False)])
def test_visible_pdf_scope_ignores_a_claim_in_an_embedded_model(
    tmp_path: Path, scope: int, chart: int, passed: bool
) -> None:
    path = tmp_path / "control.pdf"
    canvas = Canvas(str(path))
    canvas.drawString(40, 760, f"Q{scope} 2026 management report")
    canvas.drawString(40, 720, f"Supplier chart - Q{chart} Monthly View")
    canvas.save()
    # An embedded assertion of the correct scope cannot override the actual visible page.
    with path.open("ab") as stream:
        stream.write(
            b"\n%COPILOT_REPORT_MODEL:"
            + base64.b64encode(json.dumps({"quarter": scope}).encode())
            + b"\n"
        )
    check = pdf_scope_assertion(path, expected_quarter=scope)
    assert check.passed is passed
    assert f"Q{chart}" in str(check.actual)
    assert check.category == "artifact"
