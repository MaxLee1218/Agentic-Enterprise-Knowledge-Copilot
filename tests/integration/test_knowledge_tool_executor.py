"""Composition checks for the production Knowledge Tool registration boundary."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import AnyHttpUrl

from copilot.bootstrap.container import build_workflow_container
from copilot.config import PROJECT_ROOT, Settings
from copilot.tools.database import DatabaseTool
from copilot.tools.knowledge import HttpKnowledgeClient, KnowledgeTool

pytestmark = pytest.mark.integration


def test_production_composition_registers_http_knowledge_without_network(
    tmp_path: Path,
) -> None:
    checkpoint_database = tmp_path / "checkpoints.db"
    with sqlite3.connect(checkpoint_database) as database:
        database.executescript(
            (PROJECT_ROOT / "migrations" / "0001_approval_requests.sql").read_text()
        )
    settings = Settings(
        app_env="production",
        database_url="sqlite:///unused.db",
        artifact_dir=tmp_path,
        checkpoint_database_path=checkpoint_database,
        rag_base_url=AnyHttpUrl("http://rag.example/"),
    )

    with build_workflow_container(settings) as container:
        registered = container.registry.get("knowledge_search")

        assert isinstance(container.knowledge_client, HttpKnowledgeClient)
        assert isinstance(container.knowledge_tool, KnowledgeTool)
        assert isinstance(container.database_tool, DatabaseTool)
        assert registered is container.knowledge_tool
        assert container.registry.get("database_query") is container.database_tool
        assert container.knowledge_client.base_url == "http://rag.example"
        assert container.knowledge_client.timeout_seconds == 10
