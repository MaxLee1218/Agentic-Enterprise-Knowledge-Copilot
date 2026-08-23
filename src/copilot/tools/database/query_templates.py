"""Trusted parameterized query templates for frozen governed schemas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, String, and_, bindparam, case, cast, func, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import aliased
from sqlalchemy.sql.functions import FunctionElement

from copilot.tools.database.errors import DatabaseQueryValidationError
from copilot.tools.database.models import (
    BusinessUnit,
    IncomingInspection,
    Invoice,
    LegalEntity,
    Payment,
    PurchaseOrder,
    Supplier,
)
from copilot.tools.database.schema_registry import (
    ACCOUNTS_PAYABLE_SCHEMA_VERSION,
    SchemaRegistry,
)


@dataclass(frozen=True, slots=True)
class QueryTemplate:
    """One compiled query template and its normalized output-column contract."""

    template_id: str
    statement: Select[Any]
    columns: tuple[tuple[str, str], ...]


class MonthPeriod(FunctionElement[str]):
    """Portable, allowlisted year-month projection for SQLite and PostgreSQL."""

    type = String()
    name = "month_period"
    inherit_cache = True


@compiles(MonthPeriod, "sqlite")
def _compile_sqlite_month_period(element: MonthPeriod, compiler: Any, **kwargs: Any) -> str:
    argument = compiler.process(next(iter(element.clauses)), **kwargs)
    return f"strftime('%Y-%m', {argument})"


@compiles(MonthPeriod, "postgresql")
def _compile_postgresql_month_period(
    element: MonthPeriod,
    compiler: Any,
    **kwargs: Any,
) -> str:
    argument = compiler.process(next(iter(element.clauses)), **kwargs)
    return f"to_char({argument}, 'YYYY-MM')"


class QueryTemplateRegistry:
    """Build only templates registered for the selected frozen schema profile."""

    def __init__(self, schema_registry: SchemaRegistry) -> None:
        self._schema_registry = schema_registry

    def build(
        self,
        template_id: str,
        *,
        filter_supplier_ids: bool,
        filter_legal_entity_ids: bool = False,
        filter_business_unit_ids: bool = False,
        filter_currency_scope: bool = False,
    ) -> QueryTemplate:
        """Build a bounded statement without accepting SQL structure from the caller."""
        if not self._schema_registry.is_template_allowed(template_id):
            raise DatabaseQueryValidationError("Query template is not registered")
        if template_id == "supplier_quality_summary_v1":
            return self._summary(filter_supplier_ids)
        if template_id == "supplier_quality_trend_v1":
            return self._trend(filter_supplier_ids)
        ap_filters = {
            "filter_supplier_ids": filter_supplier_ids,
            "filter_legal_entity_ids": filter_legal_entity_ids,
            "filter_business_unit_ids": filter_business_unit_ids,
            "filter_currency_scope": filter_currency_scope,
        }
        if self._schema_registry.schema_version == ACCOUNTS_PAYABLE_SCHEMA_VERSION:
            if template_id == "ap_invoice_population_v1":
                return self._ap_invoice_population(**ap_filters)
            if template_id == "ap_duplicate_invoice_candidates_v1":
                return self._ap_duplicate_candidates(**ap_filters)
            if template_id == "ap_invoice_po_variance_v1":
                return self._ap_invoice_po_variance(**ap_filters)
            if template_id == "ap_payment_terms_v1":
                return self._ap_payment_terms(**ap_filters)
            if template_id == "ap_payment_amount_v1":
                return self._ap_payment_amount(**ap_filters)
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
        period = MonthPeriod(IncomingInspection.inspection_date).label("period")
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

    @staticmethod
    def _ap_base_statement(
        *,
        filter_supplier_ids: bool,
        filter_legal_entity_ids: bool,
        filter_business_unit_ids: bool,
        filter_currency_scope: bool,
    ) -> Select[Any]:
        statement = (
            select()
            .select_from(Invoice)
            .join(
                Supplier,
                and_(
                    Invoice.tenant_id == Supplier.tenant_id,
                    Invoice.supplier_id == Supplier.id,
                ),
            )
            .join(
                LegalEntity,
                and_(
                    Invoice.tenant_id == LegalEntity.tenant_id,
                    Invoice.legal_entity_id == LegalEntity.id,
                ),
            )
            .join(
                BusinessUnit,
                and_(
                    Invoice.tenant_id == BusinessUnit.tenant_id,
                    Invoice.legal_entity_id == BusinessUnit.legal_entity_id,
                    Invoice.business_unit_id == BusinessUnit.id,
                ),
            )
            .where(
                Invoice.tenant_id == bindparam("tenant_id"),
                Invoice.invoice_date >= bindparam("start_date"),
                Invoice.invoice_date <= bindparam("end_date"),
            )
        )
        if filter_supplier_ids:
            statement = statement.where(
                Supplier.supplier_code.in_(bindparam("supplier_ids", expanding=True))
            )
        if filter_legal_entity_ids:
            statement = statement.where(
                LegalEntity.legal_entity_code.in_(bindparam("legal_entity_ids", expanding=True))
            )
        if filter_business_unit_ids:
            statement = statement.where(
                BusinessUnit.business_unit_code.in_(bindparam("business_unit_ids", expanding=True))
            )
        if filter_currency_scope:
            statement = statement.where(
                Invoice.currency.in_(bindparam("currency_scope", expanding=True))
            )
        return statement

    @staticmethod
    def _invoice_scope_columns() -> tuple[Any, ...]:
        return (
            cast(Invoice.id, String).label("invoice_record_key"),
            Invoice.tenant_id.label("tenant_id"),
            Supplier.supplier_code.label("supplier_id"),
            LegalEntity.legal_entity_code.label("legal_entity_id"),
            BusinessUnit.business_unit_code.label("business_unit_id"),
        )

    @staticmethod
    def _payment_aggregates() -> tuple[Any, ...]:
        return (
            func.count(Payment.id).label("payment_count"),
            func.sum(case((Payment.status == "SETTLED", 1), else_=0)).label(
                "settled_payment_count"
            ),
            func.max(case((Payment.status == "SETTLED", Payment.payment_date), else_=None)).label(
                "payment_date"
            ),
            func.max(case((Payment.status == "SETTLED", Payment.payment_amount), else_=None)).label(
                "payment_amount"
            ),
            func.max(Payment.currency).label("payment_currency"),
            func.max(Payment.status).label("payment_status"),
        )

    @staticmethod
    def _join_nonvoid_payments(statement: Select[Any]) -> Select[Any]:
        return statement.outerjoin(
            Payment,
            and_(
                Invoice.tenant_id == Payment.tenant_id,
                Invoice.id == Payment.invoice_id,
                Payment.status != "VOID",
            ),
        )

    @staticmethod
    def _join_payment_dimensions(
        statement: Select[Any],
    ) -> tuple[Select[Any], Any, Any]:
        payment_legal_entity = aliased(LegalEntity, name="payment_legal_entity")
        payment_business_unit = aliased(BusinessUnit, name="payment_business_unit")
        joined = statement.outerjoin(
            payment_legal_entity,
            and_(
                Payment.tenant_id == payment_legal_entity.tenant_id,
                Payment.legal_entity_id == payment_legal_entity.id,
            ),
        ).outerjoin(
            payment_business_unit,
            and_(
                Payment.tenant_id == payment_business_unit.tenant_id,
                Payment.legal_entity_id == payment_business_unit.legal_entity_id,
                Payment.business_unit_id == payment_business_unit.id,
            ),
        )
        return joined, payment_legal_entity, payment_business_unit

    @staticmethod
    def _payment_relationship_aggregates(
        payment_legal_entity: Any,
        payment_business_unit: Any,
    ) -> tuple[Any, ...]:
        return (
            func.max(Payment.tenant_id).label("payment_tenant_id"),
            func.max(cast(Payment.invoice_id, String)).label("payment_invoice_record_key"),
            func.max(payment_legal_entity.legal_entity_code).label("payment_legal_entity_id"),
            func.max(payment_business_unit.business_unit_code).label("payment_business_unit_id"),
        )

    def _ap_invoice_population(self, **filters: bool) -> QueryTemplate:
        eligibility_reason = case(
            (Invoice.invoice_type != "STANDARD", "UNSUPPORTED_INVOICE_TYPE"),
            (Invoice.status.not_in(("POSTED", "PAID")), "INVOICE_STATUS_INELIGIBLE"),
            (Invoice.gross_amount <= 0, "NON_POSITIVE_GROSS_AMOUNT"),
            else_="ELIGIBLE",
        ).label("eligibility_reason")
        statement = (
            self._join_nonvoid_payments(self._ap_base_statement(**filters))
            .outerjoin(
                PurchaseOrder,
                and_(
                    Invoice.tenant_id == PurchaseOrder.tenant_id,
                    Invoice.purchase_order_id == PurchaseOrder.id,
                ),
            )
            .add_columns(
                *self._invoice_scope_columns(),
                Invoice.invoice_type.label("invoice_type"),
                Invoice.invoice_date.label("invoice_date"),
                Invoice.posting_date.label("posting_date"),
                Invoice.due_date.label("due_date"),
                Invoice.net_amount.label("net_amount"),
                Invoice.tax_amount.label("tax_amount"),
                Invoice.gross_amount.label("gross_amount"),
                Invoice.currency.label("currency"),
                Invoice.status.label("invoice_status"),
                cast(PurchaseOrder.id, String).label("po_record_key"),
                PurchaseOrder.matching_basis.label("po_matching_basis"),
                PurchaseOrder.status.label("po_status"),
                *self._payment_aggregates()[:2],
                eligibility_reason,
            )
            .group_by(
                Invoice.id,
                Invoice.tenant_id,
                Supplier.supplier_code,
                LegalEntity.legal_entity_code,
                BusinessUnit.business_unit_code,
                Invoice.invoice_type,
                Invoice.invoice_date,
                Invoice.posting_date,
                Invoice.due_date,
                Invoice.net_amount,
                Invoice.tax_amount,
                Invoice.gross_amount,
                Invoice.currency,
                Invoice.status,
                PurchaseOrder.id,
                PurchaseOrder.matching_basis,
                PurchaseOrder.status,
                eligibility_reason,
            )
            .order_by(Invoice.id)
            .limit(bindparam("execution_limit"))
        )
        return QueryTemplate(
            template_id="ap_invoice_population_v1",
            statement=statement,
            columns=(
                ("invoice_record_key", "string"),
                ("tenant_id", "string"),
                ("supplier_id", "string"),
                ("legal_entity_id", "string"),
                ("business_unit_id", "string"),
                ("invoice_type", "string"),
                ("invoice_date", "date"),
                ("posting_date", "date"),
                ("due_date", "date"),
                ("net_amount", "decimal"),
                ("tax_amount", "decimal"),
                ("gross_amount", "decimal"),
                ("currency", "string"),
                ("invoice_status", "string"),
                ("po_record_key", "string"),
                ("po_matching_basis", "string"),
                ("po_status", "string"),
                ("payment_count", "integer"),
                ("settled_payment_count", "integer"),
                ("eligibility_reason", "string"),
            ),
        )

    def _ap_duplicate_candidates(self, **filters: bool) -> QueryTemplate:
        statement = (
            self._ap_base_statement(**filters)
            .add_columns(
                *self._invoice_scope_columns(),
                Invoice.normalized_invoice_number.label("normalized_invoice_number"),
                Invoice.invoice_date.label("invoice_date"),
                Invoice.gross_amount.label("gross_amount"),
                Invoice.currency.label("currency"),
                Invoice.invoice_type.label("invoice_type"),
                Invoice.status.label("invoice_status"),
            )
            .order_by(Invoice.id)
            .limit(bindparam("execution_limit"))
        )
        return QueryTemplate(
            template_id="ap_duplicate_invoice_candidates_v1",
            statement=statement,
            columns=(
                ("invoice_record_key", "string"),
                ("tenant_id", "string"),
                ("supplier_id", "string"),
                ("legal_entity_id", "string"),
                ("business_unit_id", "string"),
                ("normalized_invoice_number", "string"),
                ("invoice_date", "date"),
                ("gross_amount", "decimal"),
                ("currency", "string"),
                ("invoice_type", "string"),
                ("invoice_status", "string"),
            ),
        )

    def _ap_invoice_po_variance(self, **filters: bool) -> QueryTemplate:
        po_supplier = aliased(Supplier, name="po_supplier")
        po_legal_entity = aliased(LegalEntity, name="po_legal_entity")
        po_business_unit = aliased(BusinessUnit, name="po_business_unit")
        statement = (
            self._ap_base_statement(**filters)
            .outerjoin(
                PurchaseOrder,
                and_(
                    Invoice.tenant_id == PurchaseOrder.tenant_id,
                    Invoice.purchase_order_id == PurchaseOrder.id,
                ),
            )
            .outerjoin(
                po_supplier,
                and_(
                    PurchaseOrder.tenant_id == po_supplier.tenant_id,
                    PurchaseOrder.supplier_id == po_supplier.id,
                ),
            )
            .outerjoin(
                po_legal_entity,
                and_(
                    PurchaseOrder.tenant_id == po_legal_entity.tenant_id,
                    PurchaseOrder.legal_entity_id == po_legal_entity.id,
                ),
            )
            .outerjoin(
                po_business_unit,
                and_(
                    PurchaseOrder.tenant_id == po_business_unit.tenant_id,
                    PurchaseOrder.legal_entity_id == po_business_unit.legal_entity_id,
                    PurchaseOrder.business_unit_id == po_business_unit.id,
                ),
            )
            .add_columns(
                *self._invoice_scope_columns(),
                cast(PurchaseOrder.id, String).label("po_record_key"),
                PurchaseOrder.tenant_id.label("po_tenant_id"),
                Invoice.invoice_type.label("invoice_type"),
                Invoice.status.label("invoice_status"),
                Invoice.gross_amount.label("invoice_gross_amount"),
                Invoice.currency.label("invoice_currency"),
                PurchaseOrder.approved_amount.label("po_approved_amount"),
                PurchaseOrder.currency.label("po_currency"),
                PurchaseOrder.matching_basis.label("po_matching_basis"),
                PurchaseOrder.status.label("po_status"),
                po_supplier.supplier_code.label("po_supplier_id"),
                po_legal_entity.legal_entity_code.label("po_legal_entity_id"),
                po_business_unit.business_unit_code.label("po_business_unit_id"),
                Invoice.no_po_exception_ref.label("no_po_exception_ref"),
                Invoice.no_po_exception_approved.label("no_po_exception_approved"),
            )
            .order_by(Invoice.id)
            .limit(bindparam("execution_limit"))
        )
        return QueryTemplate(
            template_id="ap_invoice_po_variance_v1",
            statement=statement,
            columns=(
                ("invoice_record_key", "string"),
                ("tenant_id", "string"),
                ("supplier_id", "string"),
                ("legal_entity_id", "string"),
                ("business_unit_id", "string"),
                ("po_record_key", "string"),
                ("po_tenant_id", "string"),
                ("invoice_type", "string"),
                ("invoice_status", "string"),
                ("invoice_gross_amount", "decimal"),
                ("invoice_currency", "string"),
                ("po_approved_amount", "decimal"),
                ("po_currency", "string"),
                ("po_matching_basis", "string"),
                ("po_status", "string"),
                ("po_supplier_id", "string"),
                ("po_legal_entity_id", "string"),
                ("po_business_unit_id", "string"),
                ("no_po_exception_ref", "string"),
                ("no_po_exception_approved", "boolean"),
            ),
        )

    def _ap_payment_terms(self, **filters: bool) -> QueryTemplate:
        statement, payment_legal_entity, payment_business_unit = self._join_payment_dimensions(
            self._join_nonvoid_payments(self._ap_base_statement(**filters))
        )
        statement = (
            statement.add_columns(
                *self._invoice_scope_columns(),
                Invoice.invoice_type.label("invoice_type"),
                Invoice.status.label("invoice_status"),
                Invoice.invoice_date.label("invoice_date"),
                Invoice.due_date.label("due_date"),
                Invoice.payment_terms_days.label("payment_terms_days"),
                Invoice.currency.label("invoice_currency"),
                self._payment_aggregates()[0],
                self._payment_aggregates()[1],
                self._payment_aggregates()[2],
                self._payment_aggregates()[4],
                self._payment_aggregates()[5],
                *self._payment_relationship_aggregates(
                    payment_legal_entity,
                    payment_business_unit,
                ),
            )
            .group_by(
                Invoice.id,
                Invoice.tenant_id,
                Supplier.supplier_code,
                LegalEntity.legal_entity_code,
                BusinessUnit.business_unit_code,
                Invoice.invoice_type,
                Invoice.status,
                Invoice.invoice_date,
                Invoice.due_date,
                Invoice.payment_terms_days,
                Invoice.currency,
            )
            .order_by(Invoice.id)
            .limit(bindparam("execution_limit"))
        )
        return QueryTemplate(
            template_id="ap_payment_terms_v1",
            statement=statement,
            columns=(
                ("invoice_record_key", "string"),
                ("tenant_id", "string"),
                ("supplier_id", "string"),
                ("legal_entity_id", "string"),
                ("business_unit_id", "string"),
                ("invoice_type", "string"),
                ("invoice_status", "string"),
                ("invoice_date", "date"),
                ("due_date", "date"),
                ("payment_terms_days", "integer"),
                ("invoice_currency", "string"),
                ("payment_count", "integer"),
                ("settled_payment_count", "integer"),
                ("payment_date", "date"),
                ("payment_currency", "string"),
                ("payment_status", "string"),
                ("payment_tenant_id", "string"),
                ("payment_invoice_record_key", "string"),
                ("payment_legal_entity_id", "string"),
                ("payment_business_unit_id", "string"),
            ),
        )

    def _ap_payment_amount(self, **filters: bool) -> QueryTemplate:
        statement, payment_legal_entity, payment_business_unit = self._join_payment_dimensions(
            self._join_nonvoid_payments(self._ap_base_statement(**filters))
        )
        statement = (
            statement.add_columns(
                *self._invoice_scope_columns(),
                Invoice.invoice_type.label("invoice_type"),
                Invoice.status.label("invoice_status"),
                Invoice.gross_amount.label("invoice_gross_amount"),
                Invoice.currency.label("invoice_currency"),
                *self._payment_aggregates(),
                *self._payment_relationship_aggregates(
                    payment_legal_entity,
                    payment_business_unit,
                ),
            )
            .group_by(
                Invoice.id,
                Invoice.tenant_id,
                Supplier.supplier_code,
                LegalEntity.legal_entity_code,
                BusinessUnit.business_unit_code,
                Invoice.invoice_type,
                Invoice.status,
                Invoice.gross_amount,
                Invoice.currency,
            )
            .order_by(Invoice.id)
            .limit(bindparam("execution_limit"))
        )
        return QueryTemplate(
            template_id="ap_payment_amount_v1",
            statement=statement,
            columns=(
                ("invoice_record_key", "string"),
                ("tenant_id", "string"),
                ("supplier_id", "string"),
                ("legal_entity_id", "string"),
                ("business_unit_id", "string"),
                ("invoice_type", "string"),
                ("invoice_status", "string"),
                ("invoice_gross_amount", "decimal"),
                ("invoice_currency", "string"),
                ("payment_count", "integer"),
                ("settled_payment_count", "integer"),
                ("payment_date", "date"),
                ("payment_amount", "decimal"),
                ("payment_currency", "string"),
                ("payment_status", "string"),
                ("payment_tenant_id", "string"),
                ("payment_invoice_record_key", "string"),
                ("payment_legal_entity_id", "string"),
                ("payment_business_unit_id", "string"),
            ),
        )


__all__ = ["MonthPeriod", "QueryTemplate", "QueryTemplateRegistry"]
