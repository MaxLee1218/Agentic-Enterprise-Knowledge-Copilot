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
    assert "--wait" in result.stdout


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


def test_stage13_management_cli_help_and_missing_task(tmp_path: Path) -> None:
    """Management scripts expose testable help and stable missing-task errors."""
    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(project_root / "src"),
            "DATABASE_URL": "sqlite:///unused-cli-test.db",
            "ARTIFACT_DIR": str(tmp_path / "artifacts"),
            "CHECKPOINT_DATABASE_PATH": str(tmp_path / "workflow.db"),
        }
    )
    for script in (
        "scripts/inspect_task.py",
        "scripts/inspect_runtime.py",
        "scripts/smoke_agent.py",
    ):
        help_result = subprocess.run(
            [sys.executable, script, "--help"],
            cwd=project_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert help_result.returncode == 0
        assert "\x1b[" not in help_result.stdout
    missing = subprocess.run(
        [sys.executable, "scripts/inspect_task.py", "T-NOT-FOUND"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode == 1
    assert missing.stdout == ""
    assert "TASK_NOT_FOUND" in missing.stderr


def test_stage13_smoke_agent_is_offline_and_successful() -> None:
    """The Stage 13 smoke script completes with isolated deterministic dependencies."""
    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_root / "src")
    result = subprocess.run(
        [sys.executable, "scripts/smoke_agent.py"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Stage 13 smoke: passed" in result.stdout
    assert "Artifact ID:" in result.stdout


def test_run_task_accepts_without_executing_business_work_inline(tmp_path: Path) -> None:
    """CLI submission returns durable acceptance before any business outcome exists."""
    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "PYTHONPATH": str(project_root / "src"),
            "DATABASE_URL": "sqlite:///unused-failure.db",
            "PERSISTENCE_DATABASE_URL": "sqlite:///" + str(tmp_path / "runtime-failure.db"),
            "ARTIFACT_DIR": str(tmp_path / "artifacts"),
            "CHECKPOINT_DATABASE_PATH": str(tmp_path / "workflow.db"),
            "LLM_PROVIDER": "mock",
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_task.py",
            "Analyze supplier quality and report.",
            "--demo",
        ],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Task status: CREATED" in result.stdout
    assert "Runtime status: READY" in result.stdout
    assert "TASK_INFORMATION_MISSING" not in result.stderr


def test_cli_execution_requires_explicit_demo_identity(tmp_path: Path) -> None:
    """Local execution cannot silently synthesize a demo caller."""
    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "PYTHONPATH": str(project_root / "src"),
            "DATABASE_URL": "sqlite:///unused-explicit-demo.db",
            "ARTIFACT_DIR": str(tmp_path / "artifacts"),
            "CHECKPOINT_DATABASE_PATH": str(tmp_path / "workflow.db"),
            "LLM_PROVIDER": "mock",
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_task.py",
            "Analyze Q2 2026 supplier quality and generate a JSON report.",
        ],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 3
    assert "CLI execution requires --demo" in result.stderr


def test_run_supplier_quality_cli_submits_and_can_be_inspected(tmp_path: Path) -> None:
    """The composed CLI durably submits and exposes state without inline Graph execution."""
    project_root = Path(__file__).resolve().parents[2]
    artifact_dir = tmp_path / "artifacts"
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "PYTHONPATH": str(project_root / "src"),
            "DATABASE_URL": "sqlite:///unused-smoke.db",
            "PERSISTENCE_DATABASE_URL": "sqlite:///" + str(tmp_path / "runtime.db"),
            "ARTIFACT_DIR": str(artifact_dir),
            "CHECKPOINT_DATABASE_PATH": str(tmp_path / "workflow.db"),
            "LLM_PROVIDER": "mock",
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_task.py",
            (
                "Analyze Q1 2026 supplier quality deviations for SUP-001 "
                "and generate a JSON management report."
            ),
            "--demo",
        ],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Task status: CREATED" in result.stdout
    assert "Runtime status: READY" in result.stdout
    assert "Artifact path:" not in result.stdout
    generated = tuple(artifact_dir.glob("*.json"))
    assert generated == ()
    task_id_line = next(line for line in result.stdout.splitlines() if line.startswith("Task ID:"))
    task_id = task_id_line.partition(":")[2].strip()
    inspected = subprocess.run(
        [sys.executable, "scripts/inspect_task.py", task_id, "--json"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert inspected.returncode == 0, inspected.stderr
    assert f'"task_id": "{task_id}"' in inspected.stdout
    assert '"status": "CREATED"' in inspected.stdout
    assert '"runtime_status": "READY"' in inspected.stdout
    runtime = subprocess.run(
        [sys.executable, "scripts/inspect_runtime.py", task_id],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert runtime.returncode == 0, runtime.stderr
    assert '"task_status": "CREATED"' in runtime.stdout
    assert '"runtime_status": "READY"' in runtime.stdout
    assert '"status": "PENDING"' in runtime.stdout
