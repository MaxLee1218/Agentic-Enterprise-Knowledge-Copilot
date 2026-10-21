"""Post-execution scope audit of actual PDF text, independent of the embedded model."""

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from evaluation.supplier_quality_pilot.contracts import PilotAssertion
from evaluation.supplier_quality_validation.runner import CaseInput
from evaluation.supplier_quality_validation.suite import ValidationReport

PDF_AUDIT_VERSION = "visible-quarter.v1"


def pdf_scope_assertion(path: Path, *, expected_quarter: int) -> PilotAssertion:
    """Frozen v1 reports have one quarter and no supported cross-quarter analysis.

    Read actual pages, not %COPILOT_REPORT_MODEL or the product verifier. This checks
    the predeclared scope requirement; it does not score arbitrary PDF prose/layout.
    """
    try:
        pages = PdfReader(path, strict=True).pages
        labels = tuple(
            (index, label)
            for index, page in enumerate(pages, start=1)
            for label in re.findall(r"\bQ[1-4]\b", page.extract_text())
        )
        passed = bool(labels) and all(label == f"Q{expected_quarter}" for _, label in labels)
        actual = "; ".join(f"page {index}: {label}" for index, label in labels)
    except (OSError, ValueError, PyPdfError):
        passed, actual = False, "PDF_UNREADABLE"
    return PilotAssertion(
        assertion_id="pdf_visible_quarter_scope",
        category="artifact",
        passed=passed,
        expected=f"Q{expected_quarter}",
        actual=actual,
    )


def audit_pdf_reports(
    report: ValidationReport, cases: Sequence[CaseInput], *, audit_revision: str
) -> ValidationReport:
    """Append visible-scope checks in a separate view; never overwrite original outcomes."""
    scopes = {c.case_id: c.quarter for c in cases}
    if set(scopes) != {r.case_id for r in report.runs}:
        raise ValueError("audit population differs from frozen cases")
    runs = []
    for run in report.runs:
        checks = []
        for path, checksum in zip(run.artifact_paths, run.artifact_checksums.values(), strict=True):
            if Path(path).suffix.lower() != ".pdf":
                continue
            try:
                actual_hash = "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
            except OSError:
                actual_hash = "PDF_MISSING"
            checks.append(
                PilotAssertion(
                    assertion_id="pdf_audit_integrity",
                    category="artifact",
                    passed=actual_hash == checksum,
                    expected=checksum,
                    actual=actual_hash,
                )
            )
            checks.append(pdf_scope_assertion(Path(path), expected_quarter=scopes[run.case_id]))
        if any(a.assertion_id == "pdf_visible_quarter_scope" for a in run.assertions):
            raise ValueError("PDF audit already applied")
        runs.append(run.model_copy(update={"assertions": run.assertions + tuple(checks)}))
    return ValidationReport.model_validate(
        {
            **report.model_dump(exclude_computed_fields=True),
            "runs": tuple(runs),
            "limitations": report.limitations
            + (
                f"Post-execution {PDF_AUDIT_VERSION} at {audit_revision}: PDF quarter labels "
                "must match frozen scope; original automated scores and stop snapshots retained.",
            ),
        }
    )
