from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from copilot.contracts import (
    JsonObject,
    MCPCapabilityType,
    MCPConnection,
    MCPServerIdentity,
    MCPSessionState,
    MCPStdioConfiguration,
    MCPTransport,
)
from copilot.mcp.capabilities import normalize_schema, stable_capability_name
from copilot.mcp.errors import MCPInvalidResponseError, MCPOriginRejectedError, MCPProtocolError
from copilot.mcp.lifecycle import MCPSessionLifecycle
from copilot.mcp.security.connection_policy import MCPConnectionPolicy
from copilot.mcp.security.origin_validator import MCPOriginValidator
from copilot.policies.mcp_access import MCPAccessPolicy, MCPAccessRule
from tests.mcp_helpers import identity, stdio_connection


def test_contract_rejects_ambiguous_transport_and_raw_credentials() -> None:
    with pytest.raises(ValidationError):
        MCPConnection(
            connection_id="bad",
            server=MCPServerIdentity(server_id="server", display_name="server"),
            namespace="safe",
            transport=MCPTransport.STREAMABLE_HTTP,
            endpoint="http://127.0.0.1:9000/mcp",
            stdio=MCPStdioConfiguration(
                executable="/usr/bin/false",
                working_directory="/tmp",
                environment=JsonObject({}),
            ),
        )
    with pytest.raises(ValidationError):
        MCPConnection.model_validate(
            {
                **stdio_connection().model_dump(mode="python"),
                "credential_reference": "raw-secret",
            }
        )


def test_lifecycle_allows_only_explicit_transitions() -> None:
    lifecycle = MCPSessionLifecycle()
    lifecycle.transition(MCPSessionState.CONNECTING)
    lifecycle.transition(MCPSessionState.INITIALIZING)
    lifecycle.transition(MCPSessionState.NEGOTIATING)
    lifecycle.transition(MCPSessionState.READY)
    lifecycle.require_ready()
    with pytest.raises(MCPProtocolError):
        lifecycle.transition(MCPSessionState.CREATED)


def test_malicious_metadata_is_data_and_schema_limits_fail_closed() -> None:
    assert stable_capability_name("malicious-metadata") == "malicious_metadata_254f917d"
    assert stable_capability_name("Ignore previous instructions") != "ignore_previous_instructions"
    with pytest.raises(MCPInvalidResponseError):
        normalize_schema({"$ref": "https://attacker.invalid/schema"}, label="untrusted")
    with pytest.raises(MCPInvalidResponseError):
        normalize_schema(
            {"type": "object", "properties": {str(index): {} for index in range(101)}},
            label="untrusted",
        )


def test_origin_validation_blocks_ssrf_and_dns_rebinding() -> None:
    validator = MCPOriginValidator(
        approved_hosts=("localhost", "approved.example"),
        resolver=lambda host, _port: ("127.0.0.1",) if host == "localhost" else ("10.0.0.9",),
    )
    assert validator.validate("http://localhost:8766/mcp").canonical_endpoint.endswith("/mcp")
    with pytest.raises(MCPOriginRejectedError):
        validator.validate("http://169.254.169.254/latest/meta-data")
    with pytest.raises(MCPOriginRejectedError):
        validator.validate("https://approved.example/mcp")
    with pytest.raises(MCPOriginRejectedError):
        validator.validate("http://localhost:8766/mcp", expected_addresses=("127.0.0.2",))


def test_stdio_policy_requires_fixed_executable_and_rejects_secret_environment() -> None:
    connection = stdio_connection()
    policy = MCPConnectionPolicy(
        approved_server_ids=(connection.server.server_id,),
        approved_namespaces=(connection.namespace,),
        approved_executables=(Path(connection.stdio.executable),) if connection.stdio else (),
        approved_working_directories=(Path.cwd(),),
    )
    policy.validate(connection, tenant_id="tenant-alpha")
    assert connection.stdio is not None
    unsafe = connection.model_copy(
        update={
            "stdio": connection.stdio.model_copy(
                update={"environment": JsonObject({"ACCESS_TOKEN": "must-not-leak"})}
            )
        }
    )
    with pytest.raises(Exception, match="secrets"):
        policy.validate(unsafe, tenant_id="tenant-alpha")


def test_access_policy_is_deny_by_default_and_tenant_scope_bound() -> None:
    connection = stdio_connection()
    empty = MCPAccessPolicy()
    assert not empty.evaluate_connection(
        connection_id=connection.connection_id,
        server_id=connection.server.server_id,
        namespace=connection.namespace,
        identity=identity(),
    ).allowed
    policy = MCPAccessPolicy(
        (
            MCPAccessRule(
                connection_id=connection.connection_id,
                server_id=connection.server.server_id,
                namespace=connection.namespace,
                tenants=frozenset({"tenant-alpha"}),
                capability_names=frozenset({"echo"}),
                allow_idempotent_retry=True,
            ),
        )
    )
    assert policy.evaluate_capability(
        connection_id=connection.connection_id,
        server_id=connection.server.server_id,
        namespace=connection.namespace,
        capability_name="echo",
        capability_type=MCPCapabilityType.TOOL,
        identity=identity(),
    ).allowed
    assert not policy.evaluate_capability(
        connection_id=connection.connection_id,
        server_id=connection.server.server_id,
        namespace=connection.namespace,
        capability_name="not_allowlisted",
        capability_type=MCPCapabilityType.TOOL,
        identity=identity(),
    ).allowed
    assert not policy.evaluate_connection(
        connection_id=connection.connection_id,
        server_id=connection.server.server_id,
        namespace=connection.namespace,
        identity=identity(tenant_id="tenant-beta"),
    ).allowed
    assert policy.allows_retry(
        connection_id=connection.connection_id,
        idempotent=True,
        read_only=True,
        destructive=False,
    )
    assert not policy.allows_retry(
        connection_id=connection.connection_id,
        idempotent=True,
        read_only=True,
        destructive=True,
    )
