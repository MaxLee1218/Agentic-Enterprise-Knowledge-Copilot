"""Composition selects the real provider only when explicitly configured."""

from pathlib import Path

from pydantic import SecretStr

from copilot.bootstrap.container import build_workflow_container
from copilot.config import Settings


def test_deepseek_settings_construct_an_owned_planning_provider(tmp_path: Path) -> None:
    settings = Settings(
        database_url="sqlite:///unused.db",
        artifact_dir=tmp_path / "artifacts",
        checkpoint_enabled=False,
        llm_provider="deepseek",
        llm_api_key=SecretStr("secret"),
    )

    with build_workflow_container(settings) as container:
        assert container.planning_service is not None
        assert container.owned_llm_provider is not None


def test_mock_settings_keep_offline_fixed_plan_without_scripted_provider(
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url="sqlite:///unused.db",
        artifact_dir=tmp_path / "artifacts",
        checkpoint_enabled=False,
        llm_provider="mock",
    )

    with build_workflow_container(settings) as container:
        assert container.planning_service is None
        assert container.owned_llm_provider is None
