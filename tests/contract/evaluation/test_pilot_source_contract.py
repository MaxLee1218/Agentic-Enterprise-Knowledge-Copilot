"""P01's source schema keeps the required fact denominator fixed."""

import json

import pytest
from pydantic import ValidationError

from evaluation.supplier_quality_pilot.contracts import PilotSnapshot
from evaluation.supplier_quality_pilot.source import DEFAULT_SOURCE


def test_p01_rejects_source_that_silently_removes_a_required_month() -> None:
    source = json.loads(DEFAULT_SOURCE.read_text())
    source["rows"] = [
        row
        for row in source["rows"]
        if not (
            row["tenant_id"] == source["tenant_id"]
            and row["supplier_id"] == "S-200"
            and row["period"] == "2026-06"
        )
    ]

    with pytest.raises(ValidationError, match="six authorized Q2 months"):
        PilotSnapshot.model_validate(source)
