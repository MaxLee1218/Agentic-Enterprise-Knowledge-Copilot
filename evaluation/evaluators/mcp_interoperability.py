"""Deterministic Stage 18 MCP interoperability gate aggregation."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class MCPInteroperabilityObservation:
    protocol_revision: str
    sdk_version_range: str
    stdio_passed: bool
    streamable_http_passed: bool
    oauth_passed: bool
    capability_discovery_passed: bool
    governed_import_passed: bool
    governed_export_passed: bool
    session_isolation_passed: bool
    recovery_passed: bool
    client_smoke_passed: bool
    server_smoke_passed: bool


@dataclass(frozen=True, slots=True)
class MCPInteroperabilityReport:
    passed: bool
    success_rate: float
    failed_checks: tuple[str, ...]
    metadata: dict[str, object]


class MCPInteroperabilityEvaluator:
    name = "mcp_interoperability"

    def evaluate(self, observation: MCPInteroperabilityObservation) -> MCPInteroperabilityReport:
        values = asdict(observation)
        checks = {key: value for key, value in values.items() if isinstance(value, bool)}
        failed = tuple(key for key, value in checks.items() if not value)
        return MCPInteroperabilityReport(
            passed=not failed and observation.protocol_revision == "2025-11-25",
            success_rate=sum(checks.values()) / len(checks),
            failed_checks=failed,
            metadata={
                "protocol_revision": observation.protocol_revision,
                "sdk_version_range": observation.sdk_version_range,
                "transport_set": ["stdio", "streamable_http"],
                "authorization_mode": "oauth_bearer_jwt",
            },
        )


__all__ = [
    "MCPInteroperabilityEvaluator",
    "MCPInteroperabilityObservation",
    "MCPInteroperabilityReport",
]
