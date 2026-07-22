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


def test_run_supplier_quality_workflow_offline(tmp_path: Path) -> None:
    """The composed CLI should create a verified report without external services."""
    project_root = Path(__file__).resolve().parents[2]
    artifact_dir = tmp_path / "artifacts"
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(project_root / "src"),
            "DATABASE_URL": "sqlite:///unused-smoke.db",
            "ARTIFACT_DIR": str(artifact_dir),
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_task.py",
            "--task",
            "supplier-quality-analysis",
            "--supplier-id",
            "SUP-001",
            "--material-id",
            "MAT-001",
            "--time-range",
            "2026-Q1",
        ],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Task status: COMPLETED" in result.stdout
    artifact_line = next(
        line for line in result.stdout.splitlines() if line.startswith("Artifact path:")
    )
    artifact_path = Path(artifact_line.partition(":")[2].strip())
    assert artifact_path.is_file()
    assert artifact_path.read_text(encoding="utf-8").strip()
