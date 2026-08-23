"""Governed Database Tool implementing frozen domain-specific read profiles."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from time import perf_counter
from typing import cast

from pydantic import JsonValue

from copilot.contracts import (
    EvidenceContent,
    EvidenceSourceReference,
    EvidenceType,
    JsonObject,
    RiskLevel,
    ToolApprovalPolicy,
    ToolDefinition,
    ToolIdempotency,
    ToolTimeout,
)
from copilot.contracts.base import JsonMapping
from copilot.policies.data_access import DataAccessPolicy, DataAccessRequest
from copilot.tools.base import EvidenceDraft, ToolExecutionContext, ToolExecutionOutput
from copilot.tools.database.connection import DatabaseConnection
from copilot.tools.database.errors import (
    DatabaseConnectionError,
    DatabaseQueryValidationError,
    DatabaseSchemaNotFoundError,
    DatabaseStatementTimeoutError,
)
from copilot.tools.database.query_templates import QueryTemplateRegistry
from copilot.tools.database.result_normalizer import normalize_database_rows, rows_as_json
from copilot.tools.database.schema_registry import (
    ACCOUNTS_PAYABLE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SchemaRegistry,
)
from copilot.tools.database.sql_validator import SQLValidator
from copilot.tools.exceptions import (
    ToolBusinessError,
    ToolExecutionError,
    ToolPermissionError,
    ToolTimeoutError,
    ToolValidationError,
)

LOGGER = logging.getLogger(__name__)
ACCOUNTS_PAYABLE_DATABASE_CONTRACT_PROFILE = "accounts_payable_database.v1"

DATABASE_INPUT_SCHEMA: JsonMapping = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "query_template_id",
        "parameters",
        "schema_version",
        "snapshot_at",
        "row_limit",
    ],
    "properties": {
        "query_template_id": {
            "type": "string",
            "enum": ["supplier_quality_summary_v1", "supplier_quality_trend_v1"],
        },
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["tenant_id", "start_date", "end_date", "supplier_ids"],
            "properties": {
                "tenant_id": {"type": "string", "minLength": 1},
                "start_date": {"type": "string", "format": "date"},
                "end_date": {"type": "string", "format": "date"},
                "supplier_ids": {
                    "type": "array",
                    "maxItems": 100,
                    "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1},
                },
            },
        },
        "schema_version": {"type": "string", "const": SCHEMA_VERSION},
        "snapshot_at": {"type": "string", "format": "date-time"},
        "row_limit": {"type": "integer", "minimum": 1, "maximum": 10000},
    },
}

DATABASE_OUTPUT_SCHEMA: JsonMapping = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "columns",
        "rows",
        "row_count",
        "empty_result",
        "truncated",
        "query_fingerprint",
        "snapshot_at",
    ],
    "properties": {
        "columns": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "data_type"],
                "properties": {
                    "name": {"type": "string"},
                    "data_type": {"type": "string"},
                },
            },
        },
        "rows": {"type": "array", "maxItems": 10000, "items": {"type": "object"}},
        "row_count": {"type": "integer", "minimum": 0},
        "empty_result": {"type": "boolean"},
        "truncated": {"type": "boolean"},
        "query_fingerprint": {"type": "string", "minLength": 1},
        "snapshot_at": {"type": "string", "format": "date-time"},
    },
}

AP_DATABASE_INPUT_SCHEMA: JsonMapping = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "query_template_id",
        "parameters",
        "schema_version",
        "snapshot_at",
        "row_limit",
    ],
    "properties": {
        "query_template_id": {
            "type": "string",
            "enum": [
                "ap_invoice_population_v1",
                "ap_duplicate_invoice_candidates_v1",
                "ap_invoice_po_variance_v1",
                "ap_payment_terms_v1",
                "ap_payment_amount_v1",
            ],
        },
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "tenant_id",
                "start_date",
                "end_date",
                "supplier_ids",
                "legal_entity_ids",
                "business_unit_ids",
                "currency_scope",
            ],
            "properties": {
                "tenant_id": {"type": "string", "minLength": 1},
                "start_date": {"type": "string", "format": "date"},
                "end_date": {"type": "string", "format": "date"},
                "supplier_ids": {
                    "type": "array",
                    "maxItems": 100,
                    "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1},
                },
                "legal_entity_ids": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 10,
                    "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1},
                },
                "business_unit_ids": {
                    "type": "array",
                    "maxItems": 50,
                    "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1},
                },
                "currency_scope": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"type": "string", "pattern": "^[A-Z]{3}$"},
                },
            },
        },
        "schema_version": {"type": "string", "const": ACCOUNTS_PAYABLE_SCHEMA_VERSION},
        "snapshot_at": {"type": "string", "format": "date-time"},
        "row_limit": {"type": "integer", "minimum": 1, "maximum": 50000},
    },
}

AP_DATABASE_OUTPUT_SCHEMA: JsonMapping = {
    **DATABASE_OUTPUT_SCHEMA,
    "properties": {
        **cast(JsonMapping, DATABASE_OUTPUT_SCHEMA["properties"]),
        "rows": {"type": "array", "maxItems": 50000, "items": {"type": "object"}},
    },
}


class DatabaseTool:
    """Execute only approved parameterized SELECT templates through SQLAlchemy."""

    definition = ToolDefinition(
        tool_name="database_query",
        tool_version="1.0.0-sqlalchemy",
        description=(
            "Execute one approved parameterized read-only Supplier Quality query; "
            "raw SQL, writes, unregistered objects, and scope expansion are prohibited"
        ),
        input_schema=JsonObject(DATABASE_INPUT_SCHEMA),
        output_schema=JsonObject(DATABASE_OUTPUT_SCHEMA),
        risk_level=RiskLevel.MEDIUM,
        timeout=ToolTimeout(attempt_seconds=10, overall_seconds=25),
        approval_policy=ToolApprovalPolicy(
            policy_id="database-query-v1-policy",
            trigger_conditions=(
                "restricted_field",
                "supplier_count_over_100",
                "cross_organization_scope",
            ),
            approver_role="quality_data_approver",
            editable_fields=("row_limit",),
        ),
        idempotency=ToolIdempotency(
            idempotent=True,
            key_components=(
                "query_template_id",
                "parameters",
                "schema_version",
                "snapshot_at",
                "tool_version",
            ),
            reuse_window_seconds=300,
            side_effects="Read-only database access",
        ),
    )

    def __init__(
        self,
        connection: DatabaseConnection,
        *,
        statement_timeout_seconds: float = 8,
        schema_registry: SchemaRegistry | None = None,
        data_access_policy: DataAccessPolicy | None = None,
    ) -> None:
        self._connection = connection
        self._schema_registry = schema_registry or SchemaRegistry()
        if self._schema_registry.schema_version == ACCOUNTS_PAYABLE_SCHEMA_VERSION:
            self.definition = _accounts_payable_definition()
        self._templates = QueryTemplateRegistry(self._schema_registry)
        self._validator = SQLValidator(self._schema_registry)
        self._data_access_policy = data_access_policy or DataAccessPolicy()
        self._statement_timeout_seconds = statement_timeout_seconds
        self.call_count = 0

    @classmethod
    def accounts_payable(
        cls,
        connection: DatabaseConnection,
        *,
        statement_timeout_seconds: float = 8,
        data_access_policy: DataAccessPolicy | None = None,
    ) -> DatabaseTool:
        """Compose the Stage 4 AP adapter without enabling AP workflow execution."""
        return cls(
            connection,
            statement_timeout_seconds=statement_timeout_seconds,
            schema_registry=SchemaRegistry.accounts_payable(),
            data_access_policy=data_access_policy,
        )

    def check_ready(self) -> bool:
        """Return whether the approved business schema is currently reachable."""
        return self._connection.check_ready(self._schema_registry.list_tables())

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        """Run one already-authorized template and prepare minimized DATABASE evidence."""
        self.call_count += 1
        template_id = _required_text(arguments, "query_template_id")
        schema_version = _required_text(arguments, "schema_version")
        snapshot_at = _required_timestamp(arguments, "snapshot_at")
        row_limit = _required_int(arguments, "row_limit")
        parameters = _required_mapping(arguments, "parameters")
        tenant_id = _required_text_value(parameters, "tenant_id")
        start_date = _required_date_value(parameters, "start_date")
        end_date = _required_date_value(parameters, "end_date")
        supplier_ids = _required_text_list(parameters, "supplier_ids")
        is_accounts_payable = (
            self._schema_registry.schema_version == ACCOUNTS_PAYABLE_SCHEMA_VERSION
        )
        legal_entity_ids = (
            _required_text_list(parameters, "legal_entity_ids") if is_accounts_payable else ()
        )
        business_unit_ids = (
            _required_text_list(parameters, "business_unit_ids") if is_accounts_payable else ()
        )
        currency_scope = (
            _required_text_list(parameters, "currency_scope") if is_accounts_payable else ()
        )

        if schema_version != self._schema_registry.schema_version:
            raise ToolBusinessError(
                error_code="DATABASE_SCHEMA_NOT_FOUND",
                message="Requested database schema version is unavailable",
            )
        if tenant_id != context.call.tenant_id:
            raise ToolPermissionError(
                error_code="DATABASE_QUERY_DENIED",
                message="Database tenant scope does not match the authorized call",
            )
        if start_date > end_date:
            raise ToolPermissionError(
                error_code="DATABASE_QUERY_DENIED",
                message="Database date scope is invalid",
            )
        _validate_scope_contract(
            is_accounts_payable=is_accounts_payable,
            start_date=start_date,
            end_date=end_date,
            snapshot_at=snapshot_at,
            supplier_ids=supplier_ids,
            legal_entity_ids=legal_entity_ids,
            business_unit_ids=business_unit_ids,
            currency_scope=currency_scope,
            row_limit=row_limit,
        )

        started = perf_counter()
        try:
            template = self._templates.build(
                template_id,
                filter_supplier_ids=bool(supplier_ids),
                filter_legal_entity_ids=bool(legal_entity_ids),
                filter_business_unit_ids=bool(business_unit_ids),
                filter_currency_scope=bool(currency_scope),
            )
            validated = self._validator.validate(template.statement)
            expected_access = self._schema_registry.access_profile_for_template(template_id)
            if expected_access != (validated.table_names, validated.column_names):
                raise DatabaseQueryValidationError(
                    "Query template physical access differs from its frozen profile"
                )
            raw_roles = list(context.roles) or context.metadata.root.get("roles", [])
            roles = (
                tuple(item for item in raw_roles if isinstance(item, str))
                if isinstance(raw_roles, list)
                else ()
            )
            raw_purpose = context.purpose or context.metadata.root.get("purpose")
            purpose = (
                raw_purpose if isinstance(raw_purpose, str) else "supplier_quality_analysis.v1"
            )
            raw_scopes = list(context.scopes) or context.metadata.root.get("scopes", [])
            scopes = (
                tuple(item for item in raw_scopes if isinstance(item, str))
                if isinstance(raw_scopes, list)
                else ()
            )
            decision = self._data_access_policy.evaluate(
                DataAccessRequest(
                    roles=roles,
                    table_names=validated.table_names,
                    field_names=validated.column_names,
                    purpose=purpose,
                    is_demo_identity=context.metadata.root.get("is_demo_identity") is not False,
                    scopes=scopes,
                )
            )
            if not decision.allowed:
                raise ToolPermissionError(
                    error_code=decision.reason_code,
                    message=decision.reason,
                )
            self._connection.require_tables(validated.table_names)
            execution_parameters: dict[str, object] = {
                "tenant_id": tenant_id,
                "start_date": start_date,
                "end_date": end_date,
                "execution_limit": row_limit + 1,
            }
            if supplier_ids:
                execution_parameters["supplier_ids"] = supplier_ids
            if legal_entity_ids:
                execution_parameters["legal_entity_ids"] = legal_entity_ids
            if business_unit_ids:
                execution_parameters["business_unit_ids"] = business_unit_ids
            if currency_scope:
                execution_parameters["currency_scope"] = currency_scope
            database_rows = self._connection.execute_select(
                validated.statement,
                execution_parameters,
                timeout_seconds=self._statement_timeout_seconds,
            )
        except DatabaseQueryValidationError as exc:
            raise ToolPermissionError(
                error_code="DATABASE_QUERY_DENIED",
                message="Database query violates the approved read-only template",
            ) from exc
        except DatabaseSchemaNotFoundError as exc:
            raise ToolBusinessError(
                error_code="DATABASE_SCHEMA_NOT_FOUND",
                message=(
                    f"Registered {self._schema_registry.schema_version} database schema "
                    "is unavailable"
                ),
            ) from exc
        except DatabaseStatementTimeoutError as exc:
            raise ToolTimeoutError(
                error_code="DATABASE_TIMEOUT",
                message="Database query timed out",
            ) from exc
        except DatabaseConnectionError as exc:
            raise ToolExecutionError(
                error_code="DATABASE_UNAVAILABLE",
                message="Supplier quality database is unavailable",
                recoverable=True,
            ) from exc

        normalized = normalize_database_rows(
            database_rows.rows,
            row_limit=row_limit,
            preserve_decimal=is_accounts_payable,
        )
        execution_ms = round((perf_counter() - started) * 1000)
        query_fingerprint = _query_fingerprint(
            template_id=template_id,
            schema_version=schema_version,
            snapshot_at=snapshot_at,
            tenant_id=tenant_id,
            start_date=start_date,
            end_date=end_date,
            supplier_ids=supplier_ids,
            legal_entity_ids=legal_entity_ids,
            business_unit_ids=business_unit_ids,
            currency_scope=currency_scope,
            row_limit=row_limit,
        )
        rows = rows_as_json(normalized.rows)
        columns: list[JsonMapping] = [
            {"name": name, "data_type": data_type} for name, data_type in template.columns
        ]
        output = JsonObject(
            {
                "columns": cast(JsonValue, columns),
                "rows": cast(JsonValue, rows),
                "row_count": normalized.row_count,
                "empty_result": normalized.row_count == 0,
                "truncated": normalized.truncated,
                "query_fingerprint": query_fingerprint,
                "snapshot_at": snapshot_at.isoformat(),
            }
        )
        evidence = _database_evidence(
            database_name=self._connection.database_name,
            template_id=template_id,
            schema_version=schema_version,
            snapshot_at=snapshot_at,
            query_fingerprint=query_fingerprint,
            table_names=validated.table_names,
            column_names=validated.column_names,
            tenant_id=tenant_id,
            start_date=start_date,
            end_date=end_date,
            supplier_ids=supplier_ids,
            legal_entity_ids=legal_entity_ids,
            business_unit_ids=business_unit_ids,
            currency_scope=currency_scope,
            normalized_rows=rows,
            row_count=normalized.row_count,
            truncated=normalized.truncated,
        )
        LOGGER.info(
            "Database evidence prepared",
            extra={
                "event": "database_evidence_prepared",
                "tool_name": self.definition.tool_name,
                "tool_call_id": context.call.tool_call_id,
                "task_id": context.call.task_id,
                "step_id": context.call.step_id,
                "query_template_id": template_id,
                "query_fingerprint": query_fingerprint,
                "row_count": normalized.row_count,
                "truncated": normalized.truncated,
                "execution_ms": execution_ms,
            },
        )
        return ToolExecutionOutput(output=output, evidence=(evidence,))

    def close(self) -> None:
        """Release the connection pool owned by the adapter."""
        self._connection.dispose()


def _database_evidence(
    *,
    database_name: str,
    template_id: str,
    schema_version: str,
    snapshot_at: datetime,
    query_fingerprint: str,
    table_names: tuple[str, ...],
    column_names: tuple[str, ...],
    tenant_id: str,
    start_date: date,
    end_date: date,
    supplier_ids: tuple[str, ...],
    legal_entity_ids: tuple[str, ...],
    business_unit_ids: tuple[str, ...],
    currency_scope: tuple[str, ...],
    normalized_rows: list[JsonMapping],
    row_count: int,
    truncated: bool,
) -> EvidenceDraft:
    dataset_checksum = _checksum(normalized_rows)
    supplier_scope_hash = _checksum(
        sorted(supplier_ids)
        if schema_version == ACCOUNTS_PAYABLE_SCHEMA_VERSION
        else list(supplier_ids)
    )
    if schema_version == ACCOUNTS_PAYABLE_SCHEMA_VERSION:
        source_reference: JsonMapping = {
            "database_name": database_name,
            "query_template_id": template_id,
            "template_version": template_id,
            "query_fingerprint": query_fingerprint,
            "schema_version": schema_version,
            "schema_snapshot": {
                "version": schema_version,
                "snapshot_at": snapshot_at.isoformat(),
            },
            "snapshot_at": snapshot_at.isoformat(),
            "table_names": cast(JsonValue, sorted(table_names)),
            "column_names": cast(JsonValue, sorted(column_names)),
            "statement_type": "SELECT",
            "read_only": True,
            "parameter_summary": {
                "tenant_scope_hash": _checksum(tenant_id),
                "time_scope_hash": _checksum(
                    {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()}
                ),
                "supplier_count": len(supplier_ids),
                "supplier_scope_hash": supplier_scope_hash,
                "legal_entity_count": len(legal_entity_ids),
                "legal_entity_scope_hash": _checksum(sorted(legal_entity_ids)),
                "business_unit_count": len(business_unit_ids),
                "business_unit_scope_hash": _checksum(sorted(business_unit_ids)),
                "currency_count": len(currency_scope),
                "currency_scope_hash": _checksum(sorted(currency_scope)),
            },
            "row_count": row_count,
            "dataset_checksum": dataset_checksum,
        }
        content_data: JsonMapping = {
            "row_count": row_count,
            "empty_result": row_count == 0,
            "truncated": truncated,
        }
    else:
        source_reference = {
            "database_name": database_name,
            "query_template_id": template_id,
            "query_fingerprint": query_fingerprint,
            "schema_version": schema_version,
            "snapshot_at": snapshot_at.isoformat(),
            "table_names": list(table_names),
            "column_names": list(column_names),
            "statement_type": "SELECT",
            "read_only": True,
            "parameter_summary": {
                "tenant_scope_hash": _checksum(tenant_id),
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "supplier_count": len(supplier_ids),
                "supplier_scope_hash": supplier_scope_hash,
            },
            "row_count": row_count,
        }
        content_data = {
            "row_count": row_count,
            "empty_result": row_count == 0,
            "truncated": truncated,
            "inspected_count": sum(
                cast(int, row.get("inspected_count", 0)) for row in normalized_rows
            ),
            "defect_count": sum(cast(int, row.get("defect_count", 0)) for row in normalized_rows),
        }
    return EvidenceDraft(
        source_type=EvidenceType.DATABASE,
        source_reference=EvidenceSourceReference(reference=JsonObject(source_reference)),
        content=EvidenceContent(
            data=JsonObject(content_data),
            classification="CONFIDENTIAL",
            checksum=dataset_checksum,
        ),
    )


def _query_fingerprint(
    *,
    template_id: str,
    schema_version: str,
    snapshot_at: datetime,
    tenant_id: str,
    start_date: date,
    end_date: date,
    supplier_ids: tuple[str, ...],
    legal_entity_ids: tuple[str, ...],
    business_unit_ids: tuple[str, ...],
    currency_scope: tuple[str, ...],
    row_limit: int,
) -> str:
    if schema_version == ACCOUNTS_PAYABLE_SCHEMA_VERSION:
        parameters: JsonMapping = {
            "tenant_id": tenant_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "supplier_ids": cast(JsonValue, sorted(supplier_ids)),
            "legal_entity_ids": cast(JsonValue, sorted(legal_entity_ids)),
            "business_unit_ids": cast(JsonValue, sorted(business_unit_ids)),
            "currency_scope": cast(JsonValue, sorted(currency_scope)),
        }
        return _checksum(
            {
                "query_template_id": template_id,
                "schema_version": schema_version,
                "snapshot_at": snapshot_at.isoformat(),
                "parameters": parameters,
                "row_limit": row_limit,
            }
        )
    return _checksum(
        {
            "query_template_id": template_id,
            "schema_version": schema_version,
            "snapshot_at": snapshot_at.isoformat(),
            "parameters": {
                "tenant_id": tenant_id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "supplier_ids": list(supplier_ids),
            },
            "row_limit": row_limit,
        }
    )


def _accounts_payable_definition() -> ToolDefinition:
    return ToolDefinition(
        tool_name="database_query",
        tool_version="2.0.0-sqlalchemy",
        description=(
            "Execute one approved parameterized read-only Accounts Payable query; raw SQL, "
            "writes, unregistered objects, and scope expansion are prohibited"
        ),
        input_schema=JsonObject(AP_DATABASE_INPUT_SCHEMA),
        output_schema=JsonObject(AP_DATABASE_OUTPUT_SCHEMA),
        risk_level=RiskLevel.MEDIUM,
        timeout=ToolTimeout(attempt_seconds=10, overall_seconds=25),
        approval_policy=ToolApprovalPolicy(
            policy_id="accounts-payable-database-query-v1-policy",
            trigger_conditions=(
                "restricted_field",
                "cross_business_unit_scope",
                "population_over_routine_detail_threshold",
            ),
            approver_role="finance_approver",
            editable_fields=("row_limit",),
        ),
        idempotency=ToolIdempotency(
            idempotent=True,
            key_components=(
                "query_template_id",
                "parameters",
                "schema_version",
                "snapshot_at",
                "tool_version",
            ),
            reuse_window_seconds=300,
            side_effects="Read-only Accounts Payable database access",
        ),
    )


def _validate_scope_contract(
    *,
    is_accounts_payable: bool,
    start_date: date,
    end_date: date,
    snapshot_at: datetime,
    supplier_ids: tuple[str, ...],
    legal_entity_ids: tuple[str, ...],
    business_unit_ids: tuple[str, ...],
    currency_scope: tuple[str, ...],
    row_limit: int,
) -> None:
    maximum = 50000 if is_accounts_payable else 10000
    if row_limit < 1 or row_limit > maximum:
        raise ToolValidationError(f"Database row_limit must be between 1 and {maximum}")
    collections = (
        ("supplier_ids", supplier_ids, 100),
        ("legal_entity_ids", legal_entity_ids, 10),
        ("business_unit_ids", business_unit_ids, 50),
    )
    for name, values, limit in collections:
        if len(values) > limit or len(set(values)) != len(values):
            raise ToolValidationError(f"Database input field '{name}' is outside its bounds")
    if not is_accounts_payable:
        return
    if (end_date - start_date).days + 1 > 366:
        raise ToolValidationError("Accounts Payable date range must not exceed 366 days")
    if snapshot_at.date() < end_date:
        raise ToolValidationError("Accounts Payable snapshot_at must cover the complete date range")
    if not legal_entity_ids:
        raise ToolValidationError("Accounts Payable legal_entity_ids must not be empty")
    if len(set(currency_scope)) != len(currency_scope) or any(
        len(value) != 3 or not value.isascii() or not value.isalpha() or not value.isupper()
        for value in currency_scope
    ):
        raise ToolValidationError("Accounts Payable currency_scope is invalid")


def _checksum(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _required_mapping(arguments: JsonObject, key: str) -> JsonMapping:
    value = arguments.root.get(key)
    if not isinstance(value, dict):
        raise ToolValidationError(f"Database input field '{key}' must be an object")
    return value


def _required_text(arguments: JsonObject, key: str) -> str:
    return _required_text_value(arguments.root, key)


def _required_text_value(mapping: JsonMapping, key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ToolValidationError(f"Database input field '{key}' must be a non-empty string")
    return value


def _required_int(arguments: JsonObject, key: str) -> int:
    value = arguments.root.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolValidationError(f"Database input field '{key}' must be an integer")
    return value


def _required_date_value(mapping: JsonMapping, key: str) -> date:
    value = _required_text_value(mapping, key)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ToolValidationError(f"Database input field '{key}' must be an ISO date") from exc


def _required_timestamp(arguments: JsonObject, key: str) -> datetime:
    value = _required_text(arguments, key)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolValidationError(f"Database input field '{key}' must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ToolValidationError(f"Database input field '{key}' must include a timezone")
    return parsed


def _required_text_list(mapping: JsonMapping, key: str) -> tuple[str, ...]:
    value = mapping.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ToolValidationError(f"Database input field '{key}' must be a string array")
    return tuple(cast(list[str], value))


__all__ = [
    "ACCOUNTS_PAYABLE_DATABASE_CONTRACT_PROFILE",
    "AP_DATABASE_INPUT_SCHEMA",
    "AP_DATABASE_OUTPUT_SCHEMA",
    "DATABASE_INPUT_SCHEMA",
    "DATABASE_OUTPUT_SCHEMA",
    "DatabaseTool",
]
