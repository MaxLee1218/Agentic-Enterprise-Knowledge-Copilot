"""Deny-by-default Schema Registry tests."""

from copilot.tools.database.schema_registry import SCHEMA_VERSION, SchemaRegistry


def test_registry_lists_only_approved_quality_tables_and_templates() -> None:
    registry = SchemaRegistry()

    assert registry.schema_version == SCHEMA_VERSION
    assert registry.list_tables() == (
        "corrective_actions",
        "incoming_inspections",
        "supplier_deviations",
        "suppliers",
    )
    assert registry.list_templates() == (
        "supplier_quality_summary_v1",
        "supplier_quality_trend_v1",
    )


def test_registry_allows_registered_fields_and_denies_unknown_objects() -> None:
    registry = SchemaRegistry()

    assert registry.is_table_allowed("suppliers") is True
    assert registry.is_table_allowed("users") is False
    assert registry.is_column_allowed("supplier_deviations", "defect_quantity") is True
    assert registry.is_column_allowed("supplier_deviations", "password") is False
