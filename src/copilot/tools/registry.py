"""Instance-scoped, namespace-aware registry for governed tool plugins."""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock

from copilot.contracts import CapabilityName, JsonObject, RiskLevel, ToolDefinition
from copilot.tools.base import Tool, ToolExecutionContext, ToolExecutionOutput
from copilot.tools.exceptions import (
    ToolAlreadyExistsError,
    ToolDefinitionValidationError,
    ToolNotFoundError,
    ToolRegistryConflictError,
)
from copilot.tools.schema import validate_schema_definition

_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,62}[a-z0-9]$|^[a-z]$")


class RegistrationSource(StrEnum):
    """General source of one registry binding."""

    BUILTIN = "builtin"
    CONFIGURATION = "configuration"
    DISCOVERY = "discovery"


class ToolCancellationMode(StrEnum):
    """Truthful interruption behavior declared by an adapter."""

    COOPERATIVE = "cooperative"
    NON_CANCELLABLE = "non_cancellable"


@dataclass(frozen=True, slots=True)
class ToolOrigin:
    """Canonical origin identity independent of any interoperability protocol."""

    source_id: str
    origin_type: str = "local"


@dataclass(frozen=True, slots=True)
class ToolProvenance:
    """Inspectable implementation provenance retained with a registration."""

    provider: str
    revision: str
    checksum: str | None = None


@dataclass(frozen=True, slots=True)
class ToolRegistrationRequest:
    """Fully described binding accepted by register or atomic namespace refresh."""

    tool: Tool
    namespace: str = "local"
    origin: ToolOrigin = ToolOrigin(source_id="copilot", origin_type="local")
    provenance: ToolProvenance = ToolProvenance(provider="copilot", revision="built-in")
    schema_version: str = "tool-definition.v1"
    registration_source: RegistrationSource = RegistrationSource.BUILTIN
    cancellation_mode: ToolCancellationMode = ToolCancellationMode.NON_CANCELLABLE


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """Immutable snapshot entry returned for governance and audit."""

    canonical_name: str
    local_name: str
    namespace: str
    tool: Tool
    origin: ToolOrigin
    provenance: ToolProvenance
    schema_version: str
    registration_source: RegistrationSource
    cancellation_mode: ToolCancellationMode
    generation: int


def validate_tool_name(name: str) -> str:
    """Validate the canonical v1 tool-name syntax without silently normalizing it."""
    if not _TOOL_NAME_PATTERN.fullmatch(name):
        raise ToolDefinitionValidationError(
            "Tool name must be 1-64 lowercase letters, digits, or underscores "
            "and start with a letter"
        )
    return name


def validate_namespace(namespace: str) -> str:
    """Validate a stable lower-case namespace without normalizing collisions."""
    if not _NAMESPACE_PATTERN.fullmatch(namespace):
        raise ToolDefinitionValidationError(
            "Tool namespace must be 1-64 lowercase letters, digits, hyphens, or underscores"
        )
    return namespace


def canonical_tool_name(namespace: str, local_name: str) -> str:
    """Preserve frozen local names and qualify every non-local binding."""
    namespace = validate_namespace(namespace)
    local_name = validate_tool_name(local_name)
    return local_name if namespace == "local" else f"{namespace}.{local_name}"


class _NamespacedTool:
    """Expose a canonical definition while delegating to the original adapter."""

    def __init__(self, tool: Tool, canonical_name: str) -> None:
        self._tool = tool
        self.definition = tool.definition.model_copy(update={"tool_name": canonical_name})

    def execute(self, arguments: JsonObject, context: ToolExecutionContext) -> ToolExecutionOutput:
        return self._tool.execute(arguments, context)


class ToolRegistry:
    """Thread-safe, non-global mapping from approved names to tool plugins."""

    def __init__(
        self,
        allowed_names: Collection[str] | None = None,
        allowed_risk_levels: Collection[RiskLevel] | None = None,
        allowed_namespaces: Collection[str] = ("local",),
    ) -> None:
        approved = allowed_names if allowed_names is not None else tuple(CapabilityName)
        approved_risks = (
            allowed_risk_levels
            if allowed_risk_levels is not None
            else (RiskLevel.LOW, RiskLevel.MEDIUM)
        )
        self._allowed_names = frozenset(str(name) for name in approved)
        self._allowed_risk_levels = frozenset(approved_risks)
        self._allowed_namespaces = frozenset(
            validate_namespace(namespace) for namespace in allowed_namespaces
        )
        self._entries: dict[str, RegisteredTool] = {}
        self._generation = 0
        self._lock = RLock()

    @property
    def generation(self) -> int:
        """Return the current atomic registry generation."""
        with self._lock:
            return self._generation

    def register(
        self,
        tool: Tool,
        *,
        namespace: str = "local",
        origin: ToolOrigin | None = None,
        provenance: ToolProvenance | None = None,
        schema_version: str = "tool-definition.v1",
        registration_source: RegistrationSource = RegistrationSource.BUILTIN,
        cancellation_mode: ToolCancellationMode = ToolCancellationMode.NON_CANCELLABLE,
    ) -> RegisteredTool:
        """Validate and bind a plugin exactly once by its stable name."""
        request = ToolRegistrationRequest(
            tool=tool,
            namespace=namespace,
            origin=origin or ToolOrigin(source_id="copilot", origin_type="local"),
            provenance=provenance
            or ToolProvenance(provider="copilot", revision=tool.definition.tool_version),
            schema_version=schema_version,
            registration_source=registration_source,
            cancellation_mode=cancellation_mode,
        )
        prepared = self._prepare(request, generation=self.generation + 1)
        with self._lock:
            if prepared.canonical_name in self._entries:
                raise ToolAlreadyExistsError(prepared.canonical_name)
            self._generation += 1
            prepared = _with_generation(prepared, self._generation)
            self._entries[prepared.canonical_name] = prepared
            return prepared

    def unregister(self, name: str) -> None:
        """Remove a registered plugin or reject an unknown name."""
        _validate_canonical_name(name)
        with self._lock:
            if name not in self._entries:
                raise ToolNotFoundError(name)
            del self._entries[name]
            self._generation += 1

    def get(self, name: str) -> Tool:
        """Return the plugin registered under a stable name."""
        return self.registration(name).tool

    def registration(self, name: str) -> RegisteredTool:
        """Return one immutable registration snapshot."""
        _validate_canonical_name(name)
        with self._lock:
            try:
                return self._entries[name]
            except KeyError as exc:
                raise ToolNotFoundError(name) from exc

    def list(self) -> list[ToolDefinition]:
        """Return immutable definitions in deterministic name order."""
        with self._lock:
            return [self._entries[name].tool.definition for name in sorted(self._entries)]

    def contains(self, name: str) -> bool:
        """Report whether a syntactically valid name is registered."""
        _validate_canonical_name(name)
        with self._lock:
            return name in self._entries

    def registrations(self) -> tuple[RegisteredTool, ...]:
        """Return a deterministic immutable metadata snapshot."""
        with self._lock:
            return tuple(self._entries[name] for name in sorted(self._entries))

    def refresh_namespace(
        self,
        namespace: str,
        requests: Collection[ToolRegistrationRequest],
        *,
        expected_generation: int | None = None,
    ) -> tuple[RegisteredTool, ...]:
        """Validate a complete set, then replace one namespace in a single lock commit."""
        namespace = validate_namespace(namespace)
        prepared = tuple(self._prepare(request, generation=0) for request in requests)
        if any(entry.namespace != namespace for entry in prepared):
            raise ToolDefinitionValidationError("Atomic refresh cannot cross namespaces")
        names = [entry.canonical_name for entry in prepared]
        if len(names) != len(set(names)):
            raise ToolDefinitionValidationError("Atomic refresh contains a name collision")
        with self._lock:
            if expected_generation is not None and expected_generation != self._generation:
                raise ToolRegistryConflictError()
            next_entries = {
                name: entry for name, entry in self._entries.items() if entry.namespace != namespace
            }
            if set(names).intersection(next_entries):
                raise ToolDefinitionValidationError("Atomic refresh collides with another source")
            self._generation += 1
            committed = tuple(_with_generation(entry, self._generation) for entry in prepared)
            next_entries.update((entry.canonical_name, entry) for entry in committed)
            self._entries = next_entries
            return committed

    def revoke_namespace(self, namespace: str, *, expected_generation: int | None = None) -> int:
        """Remove all future invocation bindings for one origin namespace immediately."""
        namespace = validate_namespace(namespace)
        with self._lock:
            if expected_generation is not None and expected_generation != self._generation:
                raise ToolRegistryConflictError()
            names = [name for name, entry in self._entries.items() if entry.namespace == namespace]
            if not names:
                return 0
            for name in names:
                del self._entries[name]
            self._generation += 1
            return len(names)

    def _prepare(self, request: ToolRegistrationRequest, *, generation: int) -> RegisteredTool:
        definition = request.tool.definition
        namespace = validate_namespace(request.namespace)
        if namespace not in self._allowed_namespaces:
            raise ToolDefinitionValidationError(
                f"Tool namespace '{namespace}' is not approved by this registry configuration"
            )
        local_name = validate_tool_name(definition.tool_name)
        canonical_name = canonical_tool_name(namespace, local_name)
        if namespace == "local" and local_name not in self._allowed_names:
            raise ToolDefinitionValidationError(
                f"Tool '{local_name}' is not approved by this registry configuration"
            )
        if definition.risk_level not in self._allowed_risk_levels:
            raise ToolDefinitionValidationError(
                f"Risk level '{definition.risk_level}' is not approved by this registry"
            )
        if not request.origin.source_id or not request.provenance.provider:
            raise ToolDefinitionValidationError("Tool origin and provenance are required")
        if not request.schema_version:
            raise ToolDefinitionValidationError("Tool schema version is required")
        validate_schema_definition(definition.input_schema.root, "input")
        validate_schema_definition(definition.output_schema.root, "output")
        exposed: Tool = (
            request.tool if namespace == "local" else _NamespacedTool(request.tool, canonical_name)
        )
        return RegisteredTool(
            canonical_name=canonical_name,
            local_name=local_name,
            namespace=namespace,
            tool=exposed,
            origin=request.origin,
            provenance=request.provenance,
            schema_version=request.schema_version,
            registration_source=request.registration_source,
            cancellation_mode=request.cancellation_mode,
            generation=generation,
        )


def _validate_canonical_name(name: str) -> str:
    if "." not in name:
        return validate_tool_name(name)
    namespace, separator, local_name = name.partition(".")
    if not separator or "." in local_name:
        raise ToolDefinitionValidationError("Tool identifier contains an invalid namespace")
    canonical_tool_name(namespace, local_name)
    return name


def _with_generation(entry: RegisteredTool, generation: int) -> RegisteredTool:
    return RegisteredTool(
        canonical_name=entry.canonical_name,
        local_name=entry.local_name,
        namespace=entry.namespace,
        tool=entry.tool,
        origin=entry.origin,
        provenance=entry.provenance,
        schema_version=entry.schema_version,
        registration_source=entry.registration_source,
        cancellation_mode=entry.cancellation_mode,
        generation=generation,
    )


__all__ = [
    "RegisteredTool",
    "RegistrationSource",
    "ToolCancellationMode",
    "ToolOrigin",
    "ToolProvenance",
    "ToolRegistrationRequest",
    "ToolRegistry",
    "canonical_tool_name",
    "validate_namespace",
    "validate_tool_name",
]
