"""Planner manifest derives from ToolRegistry and applies visibility filtering."""

from pathlib import Path

from copilot.llm.manifest import PlannerToolManifestBuilder
from copilot.llm.prompts import planner_messages
from copilot.services.domains import builtin_domain_manifest_registry
from tests.unit.domain.ap_helpers import make_ap_contract
from tests.unit.domain.helpers import make_contract
from tests.workflow_helpers import build_test_container


def test_manifest_is_stable_registry_derived_and_permission_filtered(
    tmp_path: Path,
) -> None:
    with build_test_container(tmp_path / "artifacts") as container:
        complete = PlannerToolManifestBuilder(container.registry).build()
        filtered = PlannerToolManifestBuilder(
            container.registry,
            visibility=lambda definition: definition.tool_name != "database_query",
        ).build()

    assert [item.capability.value for item in complete.capabilities] == sorted(
        item.capability.value for item in complete.capabilities
    )
    assert {item.capability.value for item in complete.capabilities} == {
        "knowledge_search",
        "database_query",
        "analysis_engine",
        "report_generator",
    }
    assert "database_query" not in {item.capability.value for item in filtered.capabilities}
    serialized = complete.model_dump_json()
    assert "input_schema" not in serialized
    assert "output_schema" not in serialized
    assert "tool_version" not in serialized
    assert "risk_level" not in serialized
    assert "requires_approval" not in serialized


def test_planner_v3_payload_stays_lightweight_for_both_domains(tmp_path: Path) -> None:
    domains = builtin_domain_manifest_registry()
    cases = ((make_contract(), 10), (make_ap_contract(), 14))
    with build_test_container(tmp_path / "artifacts") as container:
        builder = PlannerToolManifestBuilder(container.registry)
        for contract, max_steps in cases:
            manifest = builder.build(domains.require_execution(contract))
            messages = planner_messages(
                contract=contract,
                manifest=manifest,
                max_steps=max_steps,
            )
            serialized_prompt = "".join(message.content for message in messages)

            assert len(serialized_prompt) < 4_000
            assert len(manifest.model_dump_json()) < 1_500
            assert "task_plan_schema" not in serialized_prompt
            assert "input_schema" not in serialized_prompt
            assert "output_schema" not in serialized_prompt
            assert "tool_version" not in serialized_prompt
            assert "risk_level" not in serialized_prompt
            assert "requires_approval" not in serialized_prompt
            assert "tenant_id" not in serialized_prompt
