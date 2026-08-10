"""Validation and normalization for untrusted MCP capability metadata."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import cast

from pydantic import JsonValue

from copilot.contracts import (
    JsonObject,
    MCPCapabilityNamespace,
    MCPOrigin,
    MCPPromptCapability,
    MCPProtocolRevision,
    MCPProvenance,
    MCPResourceCapability,
    MCPToolCapability,
    MCPTransport,
)
from copilot.mcp.errors import MCPInvalidResponseError
from copilot.security import ContentSourceType, PromptInjectionDetector
from copilot.tools.schema import validate_schema_definition

_SAFE_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MAX_SCHEMA_BYTES = 65_536
_MAX_SCHEMA_DEPTH = 12
_MAX_SCHEMA_NODES = 500
_MAX_PROPERTIES = 100


def stable_capability_name(external_name: str) -> str:
    """Return a deterministic collision-safe registry name for untrusted input."""
    original = external_name.strip()
    if _SAFE_TOOL_NAME.fullmatch(original):
        return original
    normalized = re.sub(r"[^a-z0-9_]+", "_", original.lower()).strip("_")
    if not normalized or not normalized[0].isalpha():
        normalized = f"capability_{normalized}"
    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:8]
    return f"{normalized[:54].rstrip('_')}_{digest}"


def endpoint_fingerprint(value: str) -> str:
    """Hash endpoint/command identity so audit does not disclose internal network details."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def schema_digest(schema: Mapping[str, JsonValue]) -> str:
    return hashlib.sha256(
        json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def normalize_schema(schema: object, *, label: str) -> JsonObject:
    """Bound and validate an external JSON Schema before registry admission."""
    if not isinstance(schema, dict):
        raise MCPInvalidResponseError(f"{label} schema must be an object")
    try:
        serialized = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise MCPInvalidResponseError(f"{label} schema is not JSON serializable") from exc
    if len(serialized.encode("utf-8")) > _MAX_SCHEMA_BYTES:
        raise MCPInvalidResponseError(f"{label} schema exceeds the size limit")
    nodes = 0

    def inspect(value: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_SCHEMA_NODES or depth > _MAX_SCHEMA_DEPTH:
            raise MCPInvalidResponseError(f"{label} schema exceeds structural limits")
        if isinstance(value, dict):
            if "$ref" in value or "$dynamicRef" in value:
                raise MCPInvalidResponseError(f"{label} schema references are unsupported")
            properties = value.get("properties")
            if isinstance(properties, dict) and len(properties) > _MAX_PROPERTIES:
                raise MCPInvalidResponseError(f"{label} schema has too many properties")
            for item in value.values():
                inspect(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                inspect(item, depth + 1)

    inspect(schema, 0)
    candidate = JsonObject(cast(dict[str, JsonValue], schema))
    try:
        validate_schema_definition(candidate.root, label)
    except Exception as exc:
        raise MCPInvalidResponseError(f"{label} schema is unsupported") from exc
    return candidate


def _safe_text(value: object, *, fallback: str, limit: int) -> str:
    text = value if isinstance(value, str) else fallback
    clean = " ".join(_CONTROL.sub(" ", text).split())
    return (clean or fallback)[:limit]


def _safe_untrusted_description(value: object, *, fallback: str, source_id: str) -> str:
    bounded = _safe_text(value, fallback=fallback, limit=2048)
    return (
        PromptInjectionDetector()
        .scan(
            bounded,
            source_type=ContentSourceType.INTERNAL_CONFIGURATION,
            source_id=source_id,
        )
        .content[:2048]
    )


class MCPCapabilityNormalizer:
    """Create stable contracts while preserving origin and provenance."""

    def __init__(
        self,
        *,
        server_id: str,
        connection_id: str,
        namespace: str,
        transport: MCPTransport,
        endpoint_identity: str,
        server_version: str | None,
        discovered_at: datetime,
    ) -> None:
        self._server_id = server_id
        self._connection_id = connection_id
        self._namespace = MCPCapabilityNamespace(namespace).root
        self._transport = transport
        self._endpoint_fingerprint = endpoint_fingerprint(endpoint_identity)
        self._server_version = server_version
        self._discovered_at = discovered_at

    def tool(
        self,
        *,
        name: str,
        title: str | None,
        description: str | None,
        input_schema: object,
        output_schema: object | None,
        annotations: Mapping[str, JsonValue] | None = None,
    ) -> MCPToolCapability:
        safe_name = stable_capability_name(name)
        normalized_input = normalize_schema(input_schema, label="MCP tool input")
        normalized_output = normalize_schema(
            output_schema or {"type": "object"}, label="MCP tool output"
        )
        annotation_values = dict(annotations or {})
        return MCPToolCapability(
            capability_id=f"{self._server_id}:tool:{safe_name}",
            name=safe_name,
            title=_safe_text(title, fallback=safe_name, limit=200) if title else None,
            description=_safe_untrusted_description(
                description,
                fallback="External MCP tool",
                source_id=f"{self._server_id}:tool:{safe_name}",
            ),
            namespace=self._namespace,
            origin=self._origin(),
            provenance=self._provenance(normalized_input.root),
            required_scopes=("mcp.tools.invoke",),
            annotations=JsonObject(annotation_values),
            input_schema=normalized_input,
            output_schema=normalized_output,
            idempotent=bool(annotation_values.get("idempotentHint", False)),
            read_only=bool(annotation_values.get("readOnlyHint", False)),
            destructive=bool(annotation_values.get("destructiveHint", False)),
        )

    def resource(
        self,
        *,
        name: str,
        title: str | None,
        description: str | None,
        uri: str,
        mime_type: str | None,
    ) -> MCPResourceCapability:
        safe_name = stable_capability_name(name)
        return MCPResourceCapability(
            capability_id=f"{self._server_id}:resource:{safe_name}",
            name=safe_name,
            title=_safe_text(title, fallback=safe_name, limit=200) if title else None,
            description=_safe_untrusted_description(
                description,
                fallback="External MCP resource",
                source_id=f"{self._server_id}:resource:{safe_name}",
            ),
            namespace=self._namespace,
            origin=self._origin(),
            provenance=self._provenance({"uri": uri}),
            required_scopes=("mcp.resources.read",),
            uri=uri[:2048],
            mime_type=mime_type[:200] if mime_type else None,
        )

    def prompt(
        self,
        *,
        name: str,
        title: str | None,
        description: str | None,
        arguments: tuple[tuple[str, bool], ...],
    ) -> MCPPromptCapability:
        safe_name = stable_capability_name(name)
        properties: dict[str, JsonValue] = {
            item_name: {"type": "string", "maxLength": 4096} for item_name, _ in arguments
        }
        required = [item_name for item_name, is_required in arguments if is_required]
        schema: dict[str, JsonValue] = {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": cast(JsonValue, required),
        }
        normalized = normalize_schema(schema, label="MCP prompt arguments")
        return MCPPromptCapability(
            capability_id=f"{self._server_id}:prompt:{safe_name}",
            name=safe_name,
            title=_safe_text(title, fallback=safe_name, limit=200) if title else None,
            description=_safe_untrusted_description(
                description,
                fallback="External MCP prompt",
                source_id=f"{self._server_id}:prompt:{safe_name}",
            ),
            namespace=self._namespace,
            origin=self._origin(),
            provenance=self._provenance(normalized.root),
            required_scopes=("mcp.prompts.read",),
            arguments_schema=normalized,
            version=self._server_version or "unversioned",
        )

    def _origin(self) -> MCPOrigin:
        return MCPOrigin(
            server_id=self._server_id,
            connection_id=self._connection_id,
            namespace=self._namespace,
            transport=self._transport,
            endpoint_fingerprint=self._endpoint_fingerprint,
        )

    def _provenance(self, schema: Mapping[str, JsonValue]) -> MCPProvenance:
        return MCPProvenance(
            protocol_revision=MCPProtocolRevision.V2025_11_25,
            server_version=self._server_version,
            schema_digest=schema_digest(schema),
            discovered_at=self._discovered_at,
        )


__all__ = [
    "MCPCapabilityNormalizer",
    "endpoint_fingerprint",
    "normalize_schema",
    "schema_digest",
    "stable_capability_name",
]
