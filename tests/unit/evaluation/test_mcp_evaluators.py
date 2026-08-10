from evaluation.evaluators.mcp_interoperability import (
    MCPInteroperabilityEvaluator,
    MCPInteroperabilityObservation,
)
from evaluation.evaluators.mcp_safety import MCPSafetyEvaluator, MCPSafetyObservation


def test_mcp_interoperability_evaluator_requires_every_gate() -> None:
    observation = MCPInteroperabilityObservation(
        protocol_revision="2025-11-25",
        sdk_version_range=">=1.29,<2.0",
        stdio_passed=True,
        streamable_http_passed=True,
        oauth_passed=True,
        capability_discovery_passed=True,
        governed_import_passed=True,
        governed_export_passed=True,
        session_isolation_passed=True,
        recovery_passed=True,
        client_smoke_passed=True,
        server_smoke_passed=False,
    )
    report = MCPInteroperabilityEvaluator().evaluate(observation)
    assert not report.passed
    assert report.failed_checks == ("server_smoke_passed",)


def test_mcp_safety_evaluator_reports_isolation_and_leakage_failures() -> None:
    report = MCPSafetyEvaluator().evaluate(
        MCPSafetyObservation(
            deny_by_default_passed=True,
            token_leakage_passed=False,
            prompt_injection_passed=True,
            invalid_json_rpc_passed=True,
            origin_validation_passed=True,
            cross_tenant_passed=False,
            cross_server_passed=True,
            privilege_escalation_passed=True,
            approval_bypass_passed=True,
            timeout_cancellation_passed=True,
        )
    )
    assert not report.passed
    assert report.leakage_rate == 1.0
    assert report.isolation_failure_rate == 0.5
