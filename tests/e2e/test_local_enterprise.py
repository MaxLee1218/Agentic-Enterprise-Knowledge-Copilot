"""Opt-in real HTTP verification for the Local Enterprise Compose environment."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_URL = os.environ.get("LOCAL_ENTERPRISE_FRONTEND_URL")
REQUIRE_FORMAL_RAG = os.environ.get("LOCAL_ENTERPRISE_REQUIRE_FORMAL_RAG") == "1"
LOCAL_ENTERPRISE_ENV_FILE = os.environ.get("LOCAL_ENTERPRISE_ENV_FILE", ".env.local-enterprise")

pytestmark = pytest.mark.skipif(
    not FRONTEND_URL,
    reason="LOCAL_ENTERPRISE_FRONTEND_URL is not configured",
)


def test_local_enterprise_browser_facing_e2e() -> None:
    """Require the frozen four-tool chain, three Evidence types, and JSON/PDF Artifacts."""
    assert FRONTEND_URL is not None
    command = [
        sys.executable,
        "scripts/local_enterprise_smoke.py",
        "--base-url",
        FRONTEND_URL,
    ]
    if REQUIRE_FORMAL_RAG:
        command.extend(("--require-formal-rag", "--env-file", LOCAL_ENTERPRISE_ENV_FILE))
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
    )
    assert result.returncode == 0
