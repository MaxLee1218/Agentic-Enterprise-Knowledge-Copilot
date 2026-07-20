"""Registry behavior and governance validation tests."""

import pytest

from copilot.contracts import RiskLevel
from copilot.tools.exceptions import (
    ToolAlreadyExistsError,
    ToolDefinitionValidationError,
    ToolNotFoundError,
)
from copilot.tools.registry import ToolRegistry, validate_tool_name
from tests.mocks.mock_tools import MockDatabaseTool, MockKnowledgeTool


def test_register_get_list_contains_and_unregister() -> None:
    registry = ToolRegistry()
    knowledge = MockKnowledgeTool()
    database = MockDatabaseTool()

    registry.register(knowledge)
    registry.register(database)

    assert registry.get("knowledge_search") is knowledge
    assert registry.contains("database_query") is True
    assert [item.tool_name for item in registry.list()] == ["database_query", "knowledge_search"]

    registry.unregister("knowledge_search")
    assert registry.contains("knowledge_search") is False


def test_duplicate_registration_is_rejected() -> None:
    registry = ToolRegistry()
    registry.register(MockKnowledgeTool())

    with pytest.raises(ToolAlreadyExistsError):
        registry.register(MockKnowledgeTool())


def test_unknown_tool_lookup_and_unregister_are_rejected() -> None:
    registry = ToolRegistry()

    with pytest.raises(ToolNotFoundError):
        registry.get("knowledge_search")
    with pytest.raises(ToolNotFoundError):
        registry.unregister("knowledge_search")


@pytest.mark.parametrize(
    "name", ["", "knowledge search", "knowledge.search", "UPPER_CASE", "a" * 65]
)
def test_invalid_tool_names_are_rejected(name: str) -> None:
    with pytest.raises(ToolDefinitionValidationError):
        validate_tool_name(name)


def test_registry_rejects_names_outside_its_approved_capability_set() -> None:
    registry = ToolRegistry(allowed_names=("database_query",))

    with pytest.raises(ToolDefinitionValidationError, match="not approved"):
        registry.register(MockKnowledgeTool())


def test_registry_rejects_high_risk_tools_in_frozen_v1() -> None:
    tool = MockKnowledgeTool()
    tool.definition = tool.definition.model_copy(update={"risk_level": RiskLevel.HIGH})

    with pytest.raises(ToolDefinitionValidationError, match="Risk level"):
        ToolRegistry().register(tool)
