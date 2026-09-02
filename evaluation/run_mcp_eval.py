"""Run hermetic real-protocol Stage 18 interoperability and safety evaluation gates."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))
    sys.path.insert(0, str(project_root / "src"))

from evaluation.evaluators.mcp_interoperability import (
    MCPInteroperabilityEvaluator,
    MCPInteroperabilityObservation,
)
from evaluation.evaluators.mcp_safety import MCPSafetyEvaluator, MCPSafetyObservation

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(targets: tuple[str, ...]) -> bool:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *targets],
        cwd=PROJECT_ROOT,
        check=False,
        timeout=180,
    )
    return completed.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("evaluation/reports/mcp-latest.json"))
    args = parser.parse_args()
    interoperability_passed = _run(
        (
            "tests/contract/mcp",
            "tests/integration/mcp/test_real_protocol.py",
            "tests/integration/mcp/test_real_client_primitives.py",
            "tests/integration/mcp/test_governed_import.py",
            "tests/integration/mcp/test_governed_export_provider.py",
            "tests/integration/mcp/test_real_oauth_export.py",
            "tests/smoke/mcp",
        )
    )
    safety_passed = _run(
        (
            "tests/unit/mcp",
            "tests/security/mcp",
            "tests/integration/mcp/test_real_resilience_and_isolation.py",
        )
    )
    interoperability = MCPInteroperabilityEvaluator().evaluate(
        MCPInteroperabilityObservation(
            protocol_revision="2025-11-25",
            sdk_version_range=">=1.29,<2.0",
            stdio_passed=interoperability_passed,
            streamable_http_passed=interoperability_passed,
            oauth_passed=interoperability_passed,
            capability_discovery_passed=interoperability_passed,
            governed_import_passed=interoperability_passed,
            governed_export_passed=interoperability_passed,
            session_isolation_passed=interoperability_passed,
            recovery_passed=interoperability_passed,
            client_smoke_passed=interoperability_passed,
            server_smoke_passed=interoperability_passed,
        )
    )
    safety = MCPSafetyEvaluator().evaluate(
        MCPSafetyObservation(
            deny_by_default_passed=safety_passed,
            token_leakage_passed=safety_passed,
            prompt_injection_passed=safety_passed,
            invalid_json_rpc_passed=safety_passed,
            origin_validation_passed=safety_passed,
            cross_tenant_passed=safety_passed,
            cross_server_passed=safety_passed,
            privilege_escalation_passed=safety_passed,
            approval_bypass_passed=safety_passed,
            timeout_cancellation_passed=safety_passed,
        )
    )
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "code_revision": "working-tree",
        "dataset_version": "stage18-hermetic-v1",
        "configuration": "real-sdk-hermetic",
        "protocol_revision": "2025-11-25",
        "sdk_version_range": ">=1.29,<2.0",
        "interoperability": asdict(interoperability),
        "safety": asdict(safety),
        "known_limitations": [
            "Only repository-owned hermetic MCP servers are evaluated",
            "No public internet MCP server is contacted",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    passed = interoperability.passed and safety.passed
    print(f"MCP evaluation {'passed' if passed else 'failed'}: {args.output}")
    return 0 if passed else 4


if __name__ == "__main__":
    raise SystemExit(main())
