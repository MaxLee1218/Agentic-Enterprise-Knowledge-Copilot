"""Trusted parameterized query templates for the frozen ``quality.v1`` schema."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, bindparam, func, select

from copilot.tools.database.errors import DatabaseQueryValidationError
from copilot.tools.database.models import IncomingInspection, Supplier
from copilot.tools.database.schema_registry import SchemaRegistry


@dataclass(frozen=True, slots=True)
class QueryTemplate:
    """One compiled query template and its normalized output-column contract."""

    template_id: str
    statement: Select[Any]
    columns: tuple[tuple[str, str], ...]


class QueryTemplateRegistry:
    """Build only the two parameterized read-only templates frozen for v1.0."""

    def __init__(self, schema_registry: SchemaRegistry) -> None:
        self._schema_registry = schema_registry

    def build(self, template_id: str, *, filter_supplier_ids: bool) -> QueryTemplate:
        """Build a bounded statement without accepting SQL structure from the caller."""
        if not self._schema_registry.is_template_allowed(template_id):
            raise DatabaseQueryValidationError("Query template is not registered")
        if template_id == "supplier_quality_summary_v1":
            return self._summary(filter_supplier_ids)
        if template_id == "supplier_quality_trend_v1":
            return self._trend(filter_supplier_ids)
        raise DatabaseQueryValidationError("Query template is not implemented")

    @staticmethod
    def _base_statement(*, filter_supplier_ids: bool) -> Select[Any]:
        statement = (
            select()
            .select_from(IncomingInspection)
            .join(Supplier, IncomingInspection.supplier_id == Supplier.id)
            .where(
                Supplier.tenant_id == bindparam("tenant_id"),
                IncomingInspection.inspection_date >= bindparam("start_date"),
                IncomingInspection.inspection_date <= bindparam("end_date"),
            )
        )
        if filter_supplier_ids:
            statement = statement.where(
                Supplier.supplier_code.in_(bindparam("supplier_ids", expanding=True))
            )
        return statement

    def _summary(self, filter_supplier_ids: bool) -> QueryTemplate:
        period = func.strftime("%Y-%m", IncomingInspection.inspection_date).label("period")
        statement = (
            self._base_statement(filter_supplier_ids=filter_supplier_ids)
            .add_columns(
                Supplier.supplier_code.label("supplier_id"),
                period,
                func.sum(IncomingInspection.total_quantity).label("inspected_count"),
                func.sum(IncomingInspection.rejected_quantity).label("defect_count"),
            )
            .group_by(Supplier.supplier_code, period)
            .order_by(Supplier.supplier_code, period)
            .limit(bindparam("execution_limit"))
        )
        return QueryTemplate(
            template_id="supplier_quality_summary_v1",
            statement=statement,
            columns=(
                ("supplier_id", "string"),
                ("period", "string"),
                ("inspected_count", "integer"),
                ("defect_count", "integer"),
            ),
        )

    def _trend(self, filter_supplier_ids: bool) -> QueryTemplate:
        period = IncomingInspection.inspection_date.label("period")
        statement = (
            self._base_statement(filter_supplier_ids=filter_supplier_ids)
            .add_columns(
                Supplier.supplier_code.label("supplier_id"),
                period,
                func.sum(IncomingInspection.total_quantity).label("inspected_count"),
                func.sum(IncomingInspection.rejected_quantity).label("defect_count"),
            )
            .group_by(Supplier.supplier_code, IncomingInspection.inspection_date)
            .order_by(Supplier.supplier_code, IncomingInspection.inspection_date)
            .limit(bindparam("execution_limit"))
        )
        return QueryTemplate(
            template_id="supplier_quality_trend_v1",
            statement=statement,
            columns=(
                ("supplier_id", "string"),
                ("period", "date"),
                ("inspected_count", "integer"),
                ("defect_count", "integer"),
            ),
        )


__all__ = ["QueryTemplate", "QueryTemplateRegistry"]
