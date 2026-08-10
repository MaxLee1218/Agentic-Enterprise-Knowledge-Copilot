"""Stable, SDK-independent contracts for governed MCP interoperability.

The protocol adapter converts official MCP SDK models at the boundary.  Everything outside
``copilot.mcp.protocol`` consumes only the values defined here so a protocol or SDK upgrade
cannot silently rewrite business contracts.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import Field, RootModel, field_validator, model_validator

from copilot.contracts.base import ImmutableContractModel, JsonObject
from copilot.contracts.validators import validate_utc_datetime

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_NAMESPACE = re.compile(r"^[a-z][a-z0-9_-]{0,62}[a-z0-9]$|^[a-z]$")


class MCPProtocolRevision(StrEnum):
    """Protocol revision intentionally frozen for the first interoperability release."""

    V2025_11_25 = "2025-11-25"


class MCPTransport(StrEnum):
    """Supported MCP wire transports."""

    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


class MCPSessionState(StrEnum):
    """Validated connection lifecycle states."""

    CREATED = "CREATED"
    CONNECTING = "CONNECTING"
    INITIALIZING = "INITIALIZING"
    NEGOTIATING = "NEGOTIATING"
    READY = "READY"
    DISCONNECTING = "DISCONNECTING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"
    RECONNECTING = "RECONNECTING"
    EXPIRED = "EXPIRED"


class MCPCapabilityType(StrEnum):
    """MCP primitives normalized at discovery."""

    TOOL = "tool"
    RESOURCE = "resource"
    PROMPT = "prompt"
    SAMPLING = "sampling"
    ELICITATION = "elicitation"
    ROOTS = "roots"


class MCPInvocationStatus(StrEnum):
    """Protocol-neutral invocation outcomes."""

    SUCCESS = "SUCCESS"
    BUSINESS_FAILURE = "BUSINESS_FAILURE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


class MCPRecoveryStatus(StrEnum):
    """Persistable non-secret recovery state."""

    NONE = "NONE"
    REQUIRED = "REQUIRED"
    IN_PROGRESS = "IN_PROGRESS"
    RESTORED = "RESTORED"
    FAILED = "FAILED"


def _validate_identifier(value: str) -> str:
    clean = value.strip()
    if not _IDENTIFIER.fullmatch(clean):
        raise ValueError("MCP identifier contains unsupported characters")
    return clean


class MCPConnectionId(RootModel[str]):
    """Typed connection identifier."""

    root: str

    @field_validator("root")
    @classmethod
    def validate_root(cls, value: str) -> str:
        return _validate_identifier(value)


class MCPCapabilityNamespace(RootModel[str]):
    """Stable, deterministic registry namespace owned by one approved server."""

    root: str

    @field_validator("root")
    @classmethod
    def validate_root(cls, value: str) -> str:
        clean = value.strip()
        if not _NAMESPACE.fullmatch(clean):
            raise ValueError("MCP namespace is invalid")
        return clean


class MCPServerIdentity(ImmutableContractModel):
    """Canonical identity asserted for an external or exported MCP server."""

    server_id: str
    display_name: str = Field(min_length=1, max_length=200)
    expected_version: str | None = Field(default=None, max_length=100)
    canonical_endpoint: str | None = Field(default=None, max_length=2048)

    _ids = field_validator("server_id")(_validate_identifier)


class MCPClientIdentity(ImmutableContractModel):
    """Authenticated client/user/tenant facts produced by an identity adapter."""

    client_id: str
    user_id: str
    tenant_id: str
    roles: tuple[str, ...] = ()
    scopes: tuple[str, ...]
    data_scope: tuple[str, ...] = ()
    purpose: str = Field(min_length=1, max_length=200)
    issuer: str | None = Field(default=None, max_length=512)
    audience: str | None = Field(default=None, max_length=512)
    subject: str | None = Field(default=None, max_length=512)
    expires_at: datetime | None = None
    authentication_source: str = Field(min_length=1, max_length=200)

    _ids = field_validator("client_id", "user_id", "tenant_id")(_validate_identifier)
    _expiry = field_validator("expires_at")(
        lambda value: validate_utc_datetime(value) if value is not None else value
    )


class MCPStdioConfiguration(ImmutableContractModel):
    """Fixed subprocess launch description; never a shell command string."""

    executable: str = Field(min_length=1, max_length=2048)
    arguments: tuple[str, ...] = ()
    working_directory: str = Field(min_length=1, max_length=2048)
    environment: JsonObject = Field(default_factory=lambda: JsonObject({}))


class MCPConnection(ImmutableContractModel):
    """Approved non-secret server connection configuration."""

    connection_id: str
    server: MCPServerIdentity
    namespace: str
    transport: MCPTransport
    protocol_revision: MCPProtocolRevision = MCPProtocolRevision.V2025_11_25
    endpoint: str | None = Field(default=None, max_length=2048)
    stdio: MCPStdioConfiguration | None = None
    credential_reference: str | None = Field(default=None, max_length=512)
    allowed_tenants: tuple[str, ...] = ()
    required_scopes: tuple[str, ...] = ("mcp.tools.invoke",)
    allow_sampling: bool = False
    allow_elicitation: bool = False
    enabled: bool = True

    _ids = field_validator("connection_id")(_validate_identifier)

    @field_validator("namespace")
    @classmethod
    def validate_namespace(cls, value: str) -> str:
        return MCPCapabilityNamespace(value).root

    @model_validator(mode="after")
    def validate_transport_configuration(self) -> MCPConnection:
        if self.transport is MCPTransport.STDIO:
            if self.stdio is None or self.endpoint is not None:
                raise ValueError("stdio connection requires stdio config and no endpoint")
        elif self.endpoint is None or self.stdio is not None:
            raise ValueError("Streamable HTTP connection requires endpoint and no stdio config")
        if self.credential_reference is not None and not self.credential_reference.startswith(
            ("env:", "secret:")
        ):
            raise ValueError("credential_reference must use an approved provider scheme")
        return self


class MCPOrigin(ImmutableContractModel):
    """Traceable capability origin without credentials or mutable display metadata."""

    server_id: str
    connection_id: str
    namespace: str
    transport: MCPTransport
    endpoint_fingerprint: str = Field(min_length=64, max_length=64)

    _ids = field_validator("server_id", "connection_id")(_validate_identifier)


class MCPProvenance(ImmutableContractModel):
    """Discovery and schema provenance retained across invocation and evidence."""

    protocol_revision: MCPProtocolRevision
    server_version: str | None = Field(default=None, max_length=100)
    schema_digest: str = Field(min_length=64, max_length=64)
    discovered_at: datetime

    _time = field_validator("discovered_at")(validate_utc_datetime)


class MCPScope(ImmutableContractModel):
    """One external scope and the internal permission it grants."""

    external_scope: str = Field(min_length=1, max_length=200)
    internal_permission: str = Field(min_length=1, max_length=200)


class MCPCapability(ImmutableContractModel):
    """Common normalized capability metadata."""

    capability_id: str
    name: str = Field(min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    description: str = Field(default="No description supplied", min_length=1, max_length=2048)
    capability_type: MCPCapabilityType
    namespace: str
    origin: MCPOrigin
    provenance: MCPProvenance
    required_scopes: tuple[str, ...] = ()
    annotations: JsonObject = Field(default_factory=lambda: JsonObject({}))

    _ids = field_validator("capability_id")(_validate_identifier)

    @field_validator("namespace")
    @classmethod
    def validate_namespace(cls, value: str) -> str:
        return MCPCapabilityNamespace(value).root

    @property
    def canonical_name(self) -> str:
        """Return the registry-safe fully qualified name."""
        return f"{self.namespace}.{self.name}"


class MCPToolCapability(MCPCapability):
    """Normalized external tool definition."""

    capability_type: MCPCapabilityType = MCPCapabilityType.TOOL
    input_schema: JsonObject
    output_schema: JsonObject = Field(default_factory=lambda: JsonObject({"type": "object"}))
    idempotent: bool = False
    read_only: bool = False
    destructive: bool = False


class MCPResourceCapability(MCPCapability):
    """Normalized resource descriptor; content remains behind a provider."""

    capability_type: MCPCapabilityType = MCPCapabilityType.RESOURCE
    uri: str = Field(min_length=1, max_length=2048)
    mime_type: str | None = Field(default=None, max_length=200)


class MCPPromptCapability(MCPCapability):
    """Normalized explicitly exported or discovered prompt descriptor."""

    capability_type: MCPCapabilityType = MCPCapabilityType.PROMPT
    arguments_schema: JsonObject = Field(default_factory=lambda: JsonObject({"type": "object"}))
    version: str = Field(min_length=1, max_length=100)


class NegotiatedCapabilitySet(ImmutableContractModel):
    """Immutable per-session capability snapshot."""

    session_id: str
    protocol_revision: MCPProtocolRevision
    server_capabilities: tuple[str, ...] = ()
    capabilities: tuple[MCPToolCapability | MCPResourceCapability | MCPPromptCapability, ...] = ()
    negotiated_at: datetime
    generation: int = Field(default=1, ge=1)

    _ids = field_validator("session_id")(_validate_identifier)
    _time = field_validator("negotiated_at")(validate_utc_datetime)


class MCPRecoveryState(ImmutableContractModel):
    """Non-secret reconnect/recovery metadata."""

    status: MCPRecoveryStatus = MCPRecoveryStatus.NONE
    reconnect_count: int = Field(default=0, ge=0)
    last_error_code: str | None = Field(default=None, max_length=100)
    last_attempt_at: datetime | None = None

    _time = field_validator("last_attempt_at")(
        lambda value: validate_utc_datetime(value) if value is not None else value
    )


class MCPSession(ImmutableContractModel):
    """Persistable session snapshot isolated to one server and tenant."""

    session_id: str
    connection_id: str
    server_id: str
    tenant_id: str
    state: MCPSessionState
    protocol_revision: MCPProtocolRevision
    transport: MCPTransport
    namespace: str
    client_identity: MCPClientIdentity
    negotiated: NegotiatedCapabilitySet | None = None
    recovery: MCPRecoveryState = Field(default_factory=MCPRecoveryState)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None

    _ids = field_validator("session_id", "connection_id", "server_id", "tenant_id")(
        _validate_identifier
    )
    _times = field_validator("created_at", "updated_at", "expires_at")(
        lambda value: validate_utc_datetime(value) if value is not None else value
    )


class MCPInvocationContext(ImmutableContractModel):
    """Trusted invocation binding shared by imported and exported execution paths."""

    connection_id: str
    session_id: str
    server_id: str
    namespace: str
    capability_name: str
    capability_type: MCPCapabilityType
    client_identity: MCPClientIdentity
    task_id: str
    trace_id: str
    step_id: str
    tool_call_id: str
    deadline_at: datetime
    approval_id: str | None = None
    attempt: int = Field(default=1, ge=1, le=3)

    _ids = field_validator(
        "connection_id",
        "session_id",
        "server_id",
        "task_id",
        "trace_id",
        "step_id",
        "tool_call_id",
    )(_validate_identifier)
    _deadline = field_validator("deadline_at")(validate_utc_datetime)


class MCPInvocation(ImmutableContractModel):
    """Protocol-independent capability invocation."""

    invocation_id: str
    capability: MCPToolCapability
    arguments: JsonObject
    context: MCPInvocationContext

    _ids = field_validator("invocation_id")(_validate_identifier)


class MCPErrorDetail(ImmutableContractModel):
    """Safe typed failure safe to persist or return across architectural boundaries."""

    error_code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=512)
    recoverable: bool = False


class MCPInvocationResult(ImmutableContractModel):
    """Normalized result from an imported or exported capability."""

    invocation_id: str
    status: MCPInvocationStatus
    output: JsonObject | None = None
    error: MCPErrorDetail | None = None
    evidence_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    started_at: datetime
    completed_at: datetime
    retry_count: int = Field(default=0, ge=0, le=2)

    _ids = field_validator("invocation_id")(_validate_identifier)
    _times = field_validator("started_at", "completed_at")(validate_utc_datetime)

    @model_validator(mode="after")
    def validate_outcome(self) -> MCPInvocationResult:
        if self.completed_at < self.started_at:
            raise ValueError("MCP invocation completion precedes its start")
        if self.status is MCPInvocationStatus.SUCCESS and self.error is not None:
            raise ValueError("successful MCP result cannot contain an error")
        if self.status is not MCPInvocationStatus.SUCCESS and self.error is None:
            raise ValueError("failed MCP result requires a typed error")
        return self

    @property
    def latency_ms(self) -> int:
        return round((self.completed_at - self.started_at).total_seconds() * 1000)


class MCPInvocationMetadata(ImmutableContractModel):
    """Minimized auditable invocation record; payloads and credentials are excluded."""

    invocation_id: str
    task_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    client_id: str
    server_id: str
    session_id: str
    protocol_revision: MCPProtocolRevision
    transport: MCPTransport
    namespace: str
    capability_name: str
    capability_type: MCPCapabilityType
    policy_decision: str
    approval_id: str | None = None
    origin: MCPOrigin
    provenance: MCPProvenance
    latency_ms: int = Field(ge=0)
    retry_count: int = Field(ge=0, le=2)
    outcome: MCPInvocationStatus
    typed_error: str | None = None
    evidence_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    timestamp: datetime

    _ids = field_validator(
        "invocation_id", "task_id", "trace_id", "tenant_id", "user_id", "client_id"
    )(_validate_identifier)
    _time = field_validator("timestamp")(validate_utc_datetime)


class MCPAccessToken(ImmutableContractModel):
    """Verified token claims returned by an authorization adapter, never the raw token."""

    identity: MCPClientIdentity
    issued_at: datetime
    token_fingerprint: str = Field(min_length=64, max_length=64)

    _time = field_validator("issued_at")(validate_utc_datetime)


__all__ = [
    "MCPAccessToken",
    "MCPCapability",
    "MCPCapabilityNamespace",
    "MCPCapabilityType",
    "MCPClientIdentity",
    "MCPConnection",
    "MCPConnectionId",
    "MCPErrorDetail",
    "MCPInvocation",
    "MCPInvocationContext",
    "MCPInvocationMetadata",
    "MCPInvocationResult",
    "MCPInvocationStatus",
    "MCPOrigin",
    "MCPPromptCapability",
    "MCPProtocolRevision",
    "MCPProvenance",
    "MCPRecoveryState",
    "MCPRecoveryStatus",
    "MCPResourceCapability",
    "MCPScope",
    "MCPServerIdentity",
    "MCPSession",
    "MCPSessionState",
    "MCPStdioConfiguration",
    "MCPToolCapability",
    "MCPTransport",
    "NegotiatedCapabilitySet",
]
