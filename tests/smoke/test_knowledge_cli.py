"""Smoke checks for standalone Knowledge CLI entry points."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "script",
    ["scripts/check_rag_health.py", "scripts/ask_knowledge.py", "scripts/warm_rag.py"],
)
def test_knowledge_cli_help(script: str) -> None:
    environment = dict(os.environ)
    source_path = str(Path(__file__).resolve().parents[2] / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (source_path, environment.get("PYTHONPATH")) if value
    )
    result = subprocess.run(
        [sys.executable, script, "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()
    assert "\x1b" not in result.stdout
