"""Planner manifest derives from ToolRegistry and applies visibility filtering."""

from pathlib import Path

from copilot.llm.manifest import PlannerToolManifestBuilder
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

    assert [tool.name for tool in complete.tools] == sorted(tool.name for tool in complete.tools)
    assert {tool.name for tool in complete.tools} == {
        "knowledge_search",
        "database_query",
        "analysis_engine",
        "report_generator",
    }
    assert "database_query" not in {tool.name for tool in filtered.tools}
    assert all(tool.input_schema.root for tool in complete.tools)
    assert all(tool.output_schema.root for tool in complete.tools)
