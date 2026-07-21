"""Smoke and regression tests for repository quality-gate scripts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from evaluation.smoke_eval import run_smoke_evaluation
from scripts.check_architecture import SOURCE_ROOT, check_architecture
from scripts.check_docs import PROJECT_ROOT, check_documentation, validate_adr


def test_documentation_checker_accepts_repository_documents() -> None:
    """The committed architecture documentation should satisfy its governance contract."""
    assert check_documentation(PROJECT_ROOT) == []


def test_adr_checker_reports_missing_required_section(tmp_path: Path) -> None:
    """An ADR without consequences must fail with an actionable error."""
    adr_path = tmp_path / "ADR-999-invalid-decision.md"
    adr_path.write_text(
        "# ADR-999: Invalid Decision\n\n"
        "## Status\n\nProposed\n\n"
        "## Context\n\nContext.\n\n"
        "## Decision\n\nDecision.\n",
        encoding="utf-8",
    )

    errors = validate_adr(adr_path)

    assert any("missing required heading '## Consequences'" in error for error in errors)


def test_architecture_checker_accepts_current_package() -> None:
    """Current production imports should comply with the accepted layer boundaries."""
    assert check_architecture(SOURCE_ROOT) == []


def test_architecture_checker_rejects_domain_framework_import(tmp_path: Path) -> None:
    """A domain module importing FastAPI must be rejected by AST analysis."""
    source_root = tmp_path / "copilot"
    contracts = source_root / "contracts"
    contracts.mkdir(parents=True)
    (contracts / "task.py").write_text("from fastapi import FastAPI\n", encoding="utf-8")

    violations = check_architecture(source_root)

    assert len(violations) == 1
    assert violations[0].imported_module == "fastapi"
    assert "framework or provider" in violations[0].reason


def test_architecture_checker_rejects_application_adapter_import(tmp_path: Path) -> None:
    """Application orchestration must not import a concrete database tool adapter."""
    source_root = tmp_path / "copilot"
    services = source_root / "services"
    services.mkdir(parents=True)
    (services / "task_service.py").write_text(
        "from copilot.tools.database.tool import DatabaseTool\n",
        encoding="utf-8",
    )

    violations = check_architecture(source_root)

    assert len(violations) == 1
    assert violations[0].imported_module == "copilot.tools.database.tool"
    assert "infrastructure" in violations[0].reason


def test_architecture_checker_rejects_mcp_business_adapter_import(tmp_path: Path) -> None:
    """MCP protocol code must not create a direct path to concrete business tools."""
    source_root = tmp_path / "copilot"
    mcp_server = source_root / "mcp" / "server"
    mcp_server.mkdir(parents=True)
    (mcp_server / "tool_provider.py").write_text(
        "from copilot.tools.knowledge.tool import KnowledgeTool\n",
        encoding="utf-8",
    )

    violations = check_architecture(source_root)

    assert len(violations) == 1
    assert violations[0].imported_module == "copilot.tools.knowledge.tool"
    assert "MCP" in violations[0].reason


def test_evaluation_smoke_generates_governed_report(tmp_path: Path) -> None:
    """The offline smoke pipeline should load cases, run metrics, and write its report."""
    report_path = tmp_path / "smoke-report.json"

    report = run_smoke_evaluation(report_path)
    persisted_report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["case_count"] == 2
    assert report["metrics"] == [{"name": "exact_match", "value": 1.0, "passed": True}]
    assert persisted_report["dataset_version"] == "evaluation-smoke.v1"
    assert persisted_report["model_provider"] == "none"


def test_evaluation_cli_help_and_smoke(tmp_path: Path) -> None:
    """The file-based CLI required by CI should expose help and complete offline."""
    help_result = subprocess.run(
        [sys.executable, "evaluation/run_eval.py", "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    smoke_result = subprocess.run(
        [
            sys.executable,
            "evaluation/run_eval.py",
            "--smoke",
            "--output",
            str(tmp_path / "cli-report.json"),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert help_result.returncode == 0
    assert "--smoke" in help_result.stdout
    assert smoke_result.returncode == 0
    assert "Evaluation smoke test passed" in smoke_result.stdout
