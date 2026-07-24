"""Smoke checks for standalone Knowledge CLI entry points."""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "script",
    ["scripts/check_rag_health.py", "scripts/ask_knowledge.py"],
)
def test_knowledge_cli_help(script: str) -> None:
    result = subprocess.run(
        [sys.executable, script, "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()
    assert "\x1b" not in result.stdout
