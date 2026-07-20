"""Smoke tests for the command-line entry point."""

import os
import subprocess
import sys
from pathlib import Path


def test_run_task_help() -> None:
    """The script entry point should render help and exit successfully."""
    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_root / "src")

    result = subprocess.run(
        [sys.executable, "scripts/run_task.py", "--help"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--task" in result.stdout
    assert "--dry-run" in result.stdout


def test_run_task_help_keeps_option_names_machine_readable_in_ci() -> None:
    """Forced terminal styling must not split option names with ANSI escape sequences."""
    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.update(
        {
            "FORCE_COLOR": "1",
            "GITHUB_ACTIONS": "true",
            "PYTHONPATH": str(project_root / "src"),
        }
    )

    result = subprocess.run(
        [sys.executable, "scripts/run_task.py", "--help"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "\x1b[" not in result.stdout
    assert "--task" in result.stdout
    assert "--dry-run" in result.stdout
