"""Read-only evaluation seams shared by the pilot and separately frozen validation."""

from datetime import date
from typing import Literal, Protocol

from evaluation.supplier_quality_pilot.contracts import SourceRow


class SourceFacts(Protocol):
    """Source-only facts accepted by database seeding and independent evidence checking."""

    @property
    def tenant_id(self) -> str: ...

    @property
    def rows(self) -> tuple[SourceRow, ...]: ...


class ReportScope(Protocol):
    """Evaluator expectations; never execution authorization or provider input."""

    @property
    def suppliers(self) -> tuple[str, ...]: ...

    @property
    def year(self) -> int: ...

    @property
    def quarter(self) -> int: ...

    @property
    def start_date(self) -> date: ...

    @property
    def end_date(self) -> date: ...

    @property
    def output_format(self) -> Literal["json", "pdf"]: ...

    @property
    def policy(self) -> bool: ...

    @property
    def expected_outcome(self) -> Literal["report", "denial"]: ...
