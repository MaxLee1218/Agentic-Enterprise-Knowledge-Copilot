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
WITH_RUNTIME_CHECKS = os.environ.get("LOCAL_ENTERPRISE_RUNTIME_CHECKS") == "1"
LOCAL_ENTERPRISE_ENV_FILE = os.environ.get("LOCAL_ENTERPRISE_ENV_FILE", ".env.local-enterprise")
REPORT_OUTPUT = os.environ.get("LOCAL_ENTERPRISE_REPORT_OUTPUT")
PROJECT_NAME = os.environ.get("LOCAL_ENTERPRISE_PROJECT_NAME")

pytestmark = pytest.mark.skipif(
    not FRONTEND_URL,
    reason="LOCAL_ENTERPRISE_FRONTEND_URL is not configured",
)


def test_local_enterprise_browser_facing_e2e() -> None:
    """Run both frozen vertical slices over the browser-facing Local Enterprise origin."""
    assert FRONTEND_URL is not None
    command = [
        sys.executable,
        "scripts/local_enterprise_smoke.py",
        "--base-url",
        FRONTEND_URL,
    ]
    if REQUIRE_FORMAL_RAG:
        command.extend(("--require-formal-rag", "--env-file", LOCAL_ENTERPRISE_ENV_FILE))
    if WITH_RUNTIME_CHECKS:
        command.extend(("--with-runtime-checks", "--env-file", LOCAL_ENTERPRISE_ENV_FILE))
    if REPORT_OUTPUT:
        command.extend(("--report-output", REPORT_OUTPUT))
    if PROJECT_NAME:
        command.extend(("--project-name", PROJECT_NAME))
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
    )
    assert result.returncode == 0
