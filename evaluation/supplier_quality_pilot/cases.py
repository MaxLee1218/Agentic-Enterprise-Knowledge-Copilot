"""Versioned experiment scopes, kept separate from the text sent to the model."""

import calendar
import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import Field

from evaluation.contracts import EvaluationModel

CASE_PATH = Path(__file__).parents[1] / "datasets/supplier_quality_pilot_v1.jsonl"


class PilotCase(EvaluationModel):
    """One predeclared request and evaluator-only scope; not an execution authorization."""

    version: Literal["supplier-quality-value-pilot.v1"] = "supplier-quality-value-pilot.v1"
    case_id: str = Field(pattern=r"^P(0[1-9]|10)$")
    request: str = Field(min_length=1)
    suppliers: tuple[str, ...] = ("S-100", "S-200")
    year: Literal[2026] = 2026
    quarter: int = Field(default=2, ge=1, le=4)
    output_format: Literal["json", "pdf"] = "json"
    policy: bool = False
    scripted_answer: str | None = None
    expected_outcome: Literal["report", "denial"] = "report"

    @property
    def start_date(self) -> date:
        """Inclusive natural-quarter start, independent of product date helpers."""
        return date(self.year, (self.quarter - 1) * 3 + 1, 1)

    @property
    def end_date(self) -> date:
        """Inclusive natural-quarter end."""
        month = self.quarter * 3
        return date(self.year, month, calendar.monthrange(self.year, month)[1])


def load_cases(path: Path = CASE_PATH) -> tuple[PilotCase, ...]:
    """Require the exact ten-case ordered population before running any model calls."""
    cases = tuple(
        PilotCase.model_validate(json.loads(line))
        for line in path.read_text().splitlines()
        if line.strip()
    )
    if tuple(c.case_id for c in cases) != tuple(f"P{i:02}" for i in range(1, 11)):
        raise ValueError("Pilot requires P01 through P10 exactly once, in order")
    return cases
