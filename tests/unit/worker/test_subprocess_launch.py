"""Portability regression coverage for the real Worker subprocess gate."""

from __future__ import annotations

import sys

from tests.integration.test_worker_hard_kill import _worker_command


def test_hard_kill_worker_uses_active_python_interpreter() -> None:
    """GitHub Actions does not create a repository-local ``.venv`` directory."""
    assert _worker_command() == (sys.executable, "-m", "copilot.worker")
