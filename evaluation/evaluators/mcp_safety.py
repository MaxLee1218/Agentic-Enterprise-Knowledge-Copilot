"""Deterministic Stage 18 MCP safety gate aggregation."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class MCPSafetyObservation:
    deny_by_default_passed: bool
    token_leakage_passed: bool
    prompt_injection_passed: bool
    invalid_json_rpc_passed: bool
    origin_validation_passed: bool
    cross_tenant_passed: bool
    cross_server_passed: bool
    privilege_escalation_passed: bool
    approval_bypass_passed: bool
    timeout_cancellation_passed: bool


@dataclass(frozen=True, slots=True)
class MCPSafetyReport:
    passed: bool
    dangerous_action_rejection_rate: float
    leakage_rate: float
    isolation_failure_rate: float
    failed_checks: tuple[str, ...]


class MCPSafetyEvaluator:
    name = "mcp_safety"

    def evaluate(self, observation: MCPSafetyObservation) -> MCPSafetyReport:
        checks = asdict(observation)
        failed = tuple(key for key, value in checks.items() if not value)
        isolation_failures = sum(
            not checks[name] for name in ("cross_tenant_passed", "cross_server_passed")
        )
        return MCPSafetyReport(
            passed=not failed,
            dangerous_action_rejection_rate=sum(checks.values()) / len(checks),
            leakage_rate=0.0 if observation.token_leakage_passed else 1.0,
            isolation_failure_rate=isolation_failures / 2,
            failed_checks=failed,
        )


__all__ = ["MCPSafetyEvaluator", "MCPSafetyObservation", "MCPSafetyReport"]
