"""Registry behavior and governance validation tests."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from copilot.contracts import (
    SUPPLIER_QUALITY_CONTRACT_PROFILES,
    CapabilityName,
    JsonObject,
    RiskLevel,
)
from copilot.tools.exceptions import (
    ToolAlreadyExistsError,
    ToolDefinitionValidationError,
    ToolNotFoundError,
)
from copilot.tools.registry import (
    RegistrationSource,
    ToolCancellationMode,
    ToolOrigin,
    ToolProvenance,
    ToolRegistrationRequest,
    ToolRegistry,
    schema_pair_fingerprint,
    validate_tool_name,
)
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


def test_profile_lookup_requires_exact_version_and_domain_profile() -> None:
    registry = ToolRegistry()
    tool = MockKnowledgeTool()
    registry.register(tool)
    profile = SUPPLIER_QUALITY_CONTRACT_PROFILES[CapabilityName.KNOWLEDGE_SEARCH]

    assert registry.get_profile("knowledge_search", tool.definition.tool_version, profile) is tool
    with pytest.raises(ToolNotFoundError):
        registry.get_profile("knowledge_search", "latest", profile)
    with pytest.raises(ToolNotFoundError):
        registry.get_profile(
            "knowledge_search",
            tool.definition.tool_version,
            "accounts_payable_policy.v1",
        )


def test_profile_lookup_rejects_unrecognized_legacy_alias() -> None:
    registry = ToolRegistry()
    tool = MockKnowledgeTool()
    tool.definition = tool.definition.model_copy(
        update={
            "input_schema": JsonObject(
                {**tool.definition.input_schema.root, "description": "unrecognized legacy shape"}
            )
        }
    )
    registry.register(tool)
    profile = SUPPLIER_QUALITY_CONTRACT_PROFILES[CapabilityName.KNOWLEDGE_SEARCH]

    with pytest.raises(ToolNotFoundError):
        registry.get_profile(
            "knowledge_search",
            f"legacy-schema-sha256:{schema_pair_fingerprint(tool.definition)}",
            profile,
        )


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


def _request(tool: MockKnowledgeTool | MockDatabaseTool) -> ToolRegistrationRequest:
    return ToolRegistrationRequest(
        tool=tool,
        namespace="partner-a",
        origin=ToolOrigin(source_id="approved-source-a", origin_type="external_service"),
        provenance=ToolProvenance(
            provider="controlled-test-provider",
            revision="2026.08",
            checksum="sha256:registry-fixture",
        ),
        schema_version="tool-definition.v1",
        registration_source=RegistrationSource.DISCOVERY,
        cancellation_mode=ToolCancellationMode.COOPERATIVE,
    )


def test_namespace_metadata_atomic_refresh_and_reliable_revocation() -> None:
    registry = ToolRegistry(allowed_namespaces=("local", "partner-a"))
    committed = registry.refresh_namespace(
        "partner-a",
        (_request(MockKnowledgeTool()), _request(MockDatabaseTool())),
    )

    assert [entry.canonical_name for entry in committed] == [
        "partner-a.knowledge_search",
        "partner-a.database_query",
    ]
    knowledge = registry.registration("partner-a.knowledge_search")
    assert knowledge.origin.source_id == "approved-source-a"
    assert knowledge.provenance.revision == "2026.08"
    assert knowledge.registration_source is RegistrationSource.DISCOVERY
    assert knowledge.cancellation_mode is ToolCancellationMode.COOPERATIVE

    generation = registry.generation
    with pytest.raises(ToolDefinitionValidationError, match="collision"):
        registry.refresh_namespace(
            "partner-a",
            (_request(MockKnowledgeTool()), _request(MockKnowledgeTool())),
        )
    assert registry.generation == generation
    assert len(registry.registrations()) == 2

    assert registry.revoke_namespace("partner-a") == 2
    with pytest.raises(ToolNotFoundError):
        registry.get("partner-a.knowledge_search")


def test_concurrent_namespace_reads_never_observe_partial_refresh_or_revoke() -> None:
    registry = ToolRegistry(allowed_namespaces=("local", "partner-a"))
    old_set = (_request(MockKnowledgeTool()), _request(MockDatabaseTool()))
    new_set = (_request(MockKnowledgeTool()),)
    registry.refresh_namespace("partner-a", old_set)
    start = Event()

    def reader() -> set[int]:
        observed: set[int] = set()
        start.wait()
        for _index in range(1000):
            observed.add(
                len(
                    tuple(
                        item for item in registry.registrations() if item.namespace == "partner-a"
                    )
                )
            )
        return observed

    def writer() -> None:
        start.wait()
        for _index in range(100):
            registry.refresh_namespace("partner-a", new_set)
            registry.refresh_namespace("partner-a", old_set)

    with ThreadPoolExecutor(max_workers=3) as pool:
        reads = [pool.submit(reader), pool.submit(reader)]
        update = pool.submit(writer)
        start.set()
        update.result()
        observed = set().union(*(future.result() for future in reads))

    assert observed.issubset({1, 2})
    assert 0 not in observed
