"""Official MCP SDK adapter for the pinned 2025-11-25 protocol revision.

This is intentionally the *only* production module allowed to import ``mcp``.  Public methods
accept and return stable contracts; SDK sessions, requests, responses, and exceptions remain
private implementation details.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable, Coroutine
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from typing import Any, Protocol, TypeVar, cast
from uuid import uuid4

import httpx
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import NotificationOptions, Server
from mcp.server.auth.middleware.bearer_auth import (
    AuthenticatedUser,
    BearerAuthBackend,
    RequireAuthMiddleware,
)
from mcp.server.auth.provider import AccessToken
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyUrl, JsonValue
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.routing import Route

from copilot.contracts import (
    JsonObject,
    MCPAccessToken,
    MCPClientIdentity,
    MCPConnection,
    MCPErrorDetail,
    MCPInvocation,
    MCPInvocationResult,
    MCPInvocationStatus,
    MCPPromptCapability,
    MCPProtocolRevision,
    MCPResourceCapability,
    MCPToolCapability,
    MCPTransport,
    NegotiatedCapabilitySet,
)
from copilot.contracts.validators import utc_now
from copilot.mcp.capabilities import MCPCapabilityNormalizer
from copilot.mcp.errors import (
    MCPAuthenticationError,
    MCPCancelledError,
    MCPConnectionError,
    MCPError,
    MCPInvalidResponseError,
    MCPInvocationError,
    MCPNegotiationError,
    MCPProtocolError,
    MCPTimeoutError,
    MCPTransportError,
)
from copilot.security.redaction import redact_for_logging

PINNED_PROTOCOL_REVISION = MCPProtocolRevision.V2025_11_25
SDK_VERSION_RANGE = ">=1.29,<2.0"
_T = TypeVar("_T")

if PINNED_PROTOCOL_REVISION.value != types.LATEST_PROTOCOL_VERSION:  # pragma: no cover
    raise RuntimeError("Installed MCP SDK default does not match the pinned protocol revision")


@dataclass(frozen=True, slots=True)
class MCPProtocolDiscovery:
    """Stable initialization and discovery result returned to the client layer."""

    session_id: str
    server_id: str
    server_version: str | None
    negotiated: NegotiatedCapabilitySet


@dataclass(frozen=True, slots=True)
class MCPResourceContent:
    """Bounded SDK-independent resource content."""

    uri: str
    mime_type: str | None
    text: str | None = None
    blob_base64: str | None = None


@dataclass(frozen=True, slots=True)
class MCPPromptResult:
    """SDK-independent prompt material returned as untrusted data."""

    description: str | None
    messages: tuple[JsonObject, ...]


class MCPSamplingCallback(Protocol):
    def __call__(self, request: JsonObject) -> JsonObject: ...


class MCPElicitationCallback(Protocol):
    def __call__(self, request: JsonObject) -> JsonObject: ...


class MCPRootsCallback(Protocol):
    def __call__(self) -> tuple[tuple[str, str | None], ...]: ...


class MCPNotificationCallback(Protocol):
    def __call__(self, event: str) -> None: ...


class MCPTokenVerifierPort(Protocol):
    """Verify a raw bearer token at the adapter edge and return only safe claims."""

    def verify(self, token: str) -> MCPAccessToken | None: ...


class MCPServerDispatch(Protocol):
    """Stable provider interface implemented outside the SDK boundary."""

    def list_tools(self, identity: MCPClientIdentity) -> tuple[MCPToolCapability, ...]: ...

    def invoke_tool(
        self,
        name: str,
        arguments: JsonObject,
        identity: MCPClientIdentity,
        metadata: JsonObject,
    ) -> MCPInvocationResult: ...

    def list_resources(self, identity: MCPClientIdentity) -> tuple[MCPResourceCapability, ...]: ...

    def read_resource(self, uri: str, identity: MCPClientIdentity) -> MCPResourceContent: ...

    def list_prompts(self, identity: MCPClientIdentity) -> tuple[MCPPromptCapability, ...]: ...

    def get_prompt(
        self, name: str, arguments: JsonObject, identity: MCPClientIdentity
    ) -> MCPPromptResult: ...


@dataclass(slots=True)
class _SDKSessionHandle:
    stack: AsyncExitStack
    session: ClientSession
    session_id_reader: Callable[[], str | None]
    discovery: MCPProtocolDiscovery
    tool_names: dict[str, str]
    resource_uris: dict[str, str]
    prompt_names: dict[str, str]


class _AsyncLoopRuntime:
    """Own one event loop thread so SDK sessions never cross server boundaries or loops."""

    def __init__(self, name: str) -> None:
        self._ready = Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = Thread(target=self._run, name=name, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5):  # pragma: no cover
            raise MCPConnectionError("MCP protocol runtime did not start")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()

    def submit(self, coroutine: Coroutine[Any, Any, _T]) -> Future[_T]:
        loop = self._loop
        if loop is None or not loop.is_running():
            coroutine.close()
            raise MCPConnectionError("MCP protocol runtime is unavailable")
        return asyncio.run_coroutine_threadsafe(coroutine, loop)

    def stop(self) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout=5)


class MCPProtocolClient:
    """Synchronous stable facade over real SDK stdio or Streamable HTTP sessions."""

    def __init__(
        self,
        connection: MCPConnection,
        *,
        connect_timeout_seconds: float = 10,
        initialize_timeout_seconds: float = 15,
        invocation_timeout_seconds: float = 60,
        sampling_callback: MCPSamplingCallback | None = None,
        elicitation_callback: MCPElicitationCallback | None = None,
        roots_callback: MCPRootsCallback | None = None,
        notification_callback: MCPNotificationCallback | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._connection = connection
        self._connect_timeout = connect_timeout_seconds
        self._initialize_timeout = initialize_timeout_seconds
        self._invocation_timeout = invocation_timeout_seconds
        self._sampling_callback = sampling_callback
        self._elicitation_callback = elicitation_callback
        self._roots_callback = roots_callback
        self._notification_callback = notification_callback
        self._clock = clock
        self._runtime = _AsyncLoopRuntime(f"mcp-{connection.connection_id}")
        self._handle: _SDKSessionHandle | None = None

    def connect(self, *, credential: str | None = None) -> MCPProtocolDiscovery:
        """Connect, initialize, negotiate, and discover all supported primitives."""
        if self._handle is not None:
            return self._handle.discovery
        future = self._runtime.submit(self._open(credential))
        try:
            handle = future.result(timeout=self._connect_timeout + self._initialize_timeout + 5)
        except FutureTimeoutError as exc:
            future.cancel()
            raise MCPTimeoutError("MCP connection or initialization timed out") from exc
        except MCPError:
            raise
        except Exception as exc:
            raise _translate_sdk_error(exc, operation="connect") from None
        self._handle = handle
        return handle.discovery

    def invoke(
        self,
        invocation: MCPInvocation,
        *,
        cancellation_requested: Callable[[], bool] | None = None,
        progress_callback: Callable[[float, float | None, str | None], None] | None = None,
    ) -> MCPInvocationResult:
        """Invoke one discovered tool on its owning session with timeout/cancellation."""
        handle = self._require_handle()
        future = self._runtime.submit(
            self._invoke(handle, invocation, progress_callback=progress_callback)
        )
        while True:
            if cancellation_requested is not None and cancellation_requested():
                future.cancel()
                raise MCPCancelledError("MCP invocation was cancelled")
            try:
                return future.result(timeout=0.05)
            except FutureTimeoutError:
                continue
            except MCPError:
                raise
            except Exception as exc:
                raise _translate_sdk_error(exc, operation="invoke") from None

    def read_resource(self, capability: MCPResourceCapability) -> tuple[MCPResourceContent, ...]:
        handle = self._require_handle()
        try:
            return self._runtime.submit(self._read_resource(handle, capability)).result(
                timeout=self._invocation_timeout
            )
        except FutureTimeoutError as exc:
            raise MCPTimeoutError("MCP resource read timed out") from exc
        except MCPError:
            raise
        except Exception as exc:
            raise _translate_sdk_error(exc, operation="read resource") from None

    def get_prompt(self, capability: MCPPromptCapability, arguments: JsonObject) -> MCPPromptResult:
        handle = self._require_handle()
        try:
            return self._runtime.submit(self._get_prompt(handle, capability, arguments)).result(
                timeout=self._invocation_timeout
            )
        except FutureTimeoutError as exc:
            raise MCPTimeoutError("MCP prompt request timed out") from exc
        except MCPError:
            raise
        except Exception as exc:
            raise _translate_sdk_error(exc, operation="get prompt") from None

    def close(self) -> None:
        """Terminate the MCP session and its isolated event-loop thread."""
        handle = self._handle
        self._handle = None
        if handle is not None:
            with suppress(Exception):
                self._runtime.submit(handle.stack.aclose()).result(timeout=10)
        self._runtime.stop()

    async def _open(self, credential: str | None) -> _SDKSessionHandle:
        stack = AsyncExitStack()
        try:

            def session_id_reader() -> str | None:
                return None

            if self._connection.transport is MCPTransport.STDIO:
                config = self._connection.stdio
                if config is None:  # pragma: no cover - contract enforces this
                    raise MCPProtocolError("stdio configuration is missing")
                streams = await stack.enter_async_context(
                    stdio_client(
                        StdioServerParameters(
                            command=config.executable,
                            args=list(config.arguments),
                            cwd=Path(config.working_directory),
                            env={key: str(value) for key, value in config.environment.root.items()},
                        )
                    )
                )
                read_stream, write_stream = streams
            else:
                endpoint = self._connection.endpoint
                if endpoint is None:  # pragma: no cover - contract enforces this
                    raise MCPProtocolError("Streamable HTTP endpoint is missing")
                headers = {"Authorization": f"Bearer {credential}"} if credential else {}
                http_client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        headers=headers,
                        follow_redirects=False,
                        trust_env=False,
                        timeout=httpx.Timeout(self._connect_timeout),
                    )
                )
                read_stream, write_stream, session_id_reader = await stack.enter_async_context(
                    streamable_http_client(endpoint, http_client=http_client)
                )

            sampling_callback = self._sdk_sampling_callback() if self._sampling_callback else None
            elicitation_callback = (
                self._sdk_elicitation_callback() if self._elicitation_callback else None
            )
            roots_callback = self._sdk_roots_callback() if self._roots_callback else None
            message_handler = self._sdk_message_handler() if self._notification_callback else None
            session = await stack.enter_async_context(
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=self._initialize_timeout),
                    sampling_callback=sampling_callback,
                    elicitation_callback=elicitation_callback,
                    list_roots_callback=roots_callback,
                    message_handler=message_handler,
                    client_info=types.Implementation(
                        name="agentic-enterprise-knowledge-copilot", version="0.1.0"
                    ),
                )
            )
            initialized = await asyncio.wait_for(
                session.initialize(), timeout=self._initialize_timeout
            )
            if str(initialized.protocolVersion) != PINNED_PROTOCOL_REVISION.value:
                raise MCPNegotiationError("MCP server did not negotiate the pinned revision")
            discovered_at = self._clock()
            server_id = _safe_identifier(initialized.serverInfo.name, prefix="server")
            if server_id != self._connection.server.server_id:
                raise MCPNegotiationError(
                    "MCP initialize server identity does not match the approved configuration"
                )
            endpoint_identity = (
                f"{self._connection.stdio.executable}:{self._connection.stdio.arguments}"
                if self._connection.stdio is not None
                else self._connection.endpoint or self._connection.connection_id
            )
            normalizer = MCPCapabilityNormalizer(
                server_id=server_id,
                connection_id=self._connection.connection_id,
                namespace=self._connection.namespace,
                transport=self._connection.transport,
                endpoint_identity=endpoint_identity,
                server_version=initialized.serverInfo.version,
                discovered_at=discovered_at,
            )
            capabilities: list[MCPToolCapability | MCPResourceCapability | MCPPromptCapability] = []
            tool_names: dict[str, str] = {}
            resource_uris: dict[str, str] = {}
            prompt_names: dict[str, str] = {}

            for tool_item in (await session.list_tools()).tools:
                annotations = (
                    cast(dict[str, JsonValue], tool_item.annotations.model_dump(mode="json"))
                    if tool_item.annotations is not None
                    else {}
                )
                normalized = normalizer.tool(
                    name=tool_item.name,
                    title=tool_item.title,
                    description=tool_item.description,
                    input_schema=tool_item.inputSchema,
                    output_schema=tool_item.outputSchema,
                    annotations=annotations,
                )
                if normalized.name in tool_names:
                    raise MCPNegotiationError("MCP tool names normalize to a collision")
                tool_names[normalized.name] = tool_item.name
                capabilities.append(normalized)
            if initialized.capabilities.resources is not None:
                for resource_item in (await session.list_resources()).resources:
                    normalized_resource = normalizer.resource(
                        name=resource_item.name,
                        title=resource_item.title,
                        description=resource_item.description,
                        uri=str(resource_item.uri),
                        mime_type=resource_item.mimeType,
                    )
                    resource_uris[normalized_resource.name] = str(resource_item.uri)
                    capabilities.append(normalized_resource)
            if initialized.capabilities.prompts is not None:
                for prompt_item in (await session.list_prompts()).prompts:
                    normalized_prompt = normalizer.prompt(
                        name=prompt_item.name,
                        title=prompt_item.title,
                        description=prompt_item.description,
                        arguments=tuple(
                            (argument.name, bool(argument.required))
                            for argument in (prompt_item.arguments or [])
                        ),
                    )
                    prompt_names[normalized_prompt.name] = prompt_item.name
                    capabilities.append(normalized_prompt)
            session_id = session_id_reader() or f"MCP-{uuid4().hex}"
            server_capabilities = tuple(
                name
                for name, enabled in (
                    ("tools", initialized.capabilities.tools is not None),
                    ("resources", initialized.capabilities.resources is not None),
                    ("prompts", initialized.capabilities.prompts is not None),
                    ("logging", initialized.capabilities.logging is not None),
                )
                if enabled
            )
            negotiated = NegotiatedCapabilitySet(
                session_id=session_id,
                protocol_revision=PINNED_PROTOCOL_REVISION,
                server_capabilities=server_capabilities,
                capabilities=tuple(capabilities),
                negotiated_at=discovered_at,
            )
            discovery = MCPProtocolDiscovery(
                session_id=session_id,
                server_id=server_id,
                server_version=initialized.serverInfo.version,
                negotiated=negotiated,
            )
            return _SDKSessionHandle(
                stack=stack,
                session=session,
                session_id_reader=session_id_reader,
                discovery=discovery,
                tool_names=tool_names,
                resource_uris=resource_uris,
                prompt_names=prompt_names,
            )
        except Exception:
            await stack.aclose()
            raise

    async def _invoke(
        self,
        handle: _SDKSessionHandle,
        invocation: MCPInvocation,
        *,
        progress_callback: Callable[[float, float | None, str | None], None] | None,
    ) -> MCPInvocationResult:
        external_name = handle.tool_names.get(invocation.capability.name)
        if external_name is None:
            raise MCPInvocationError("MCP capability is not present in this session")
        started_at = self._clock()

        async def report_progress(
            progress: float, total: float | None, message: str | None
        ) -> None:
            if progress_callback is not None:
                await asyncio.to_thread(progress_callback, progress, total, message)

        context = invocation.context
        meta = {
            "copilot": {
                "connection_id": context.connection_id,
                "session_id": context.session_id,
                "server_id": context.server_id,
                "namespace": context.namespace,
                "task_id": context.task_id,
                "trace_id": context.trace_id,
                "step_id": context.step_id,
                "tool_call_id": context.tool_call_id,
                "tenant_id": context.client_identity.tenant_id,
                "user_id": context.client_identity.user_id,
                "approval_id": context.approval_id,
                "purpose": context.client_identity.purpose,
            }
        }
        try:
            result = await asyncio.wait_for(
                handle.session.call_tool(
                    external_name,
                    arguments=dict(invocation.arguments.root),
                    progress_callback=report_progress,
                    meta=meta,
                ),
                timeout=min(
                    self._invocation_timeout,
                    max(0.001, (context.deadline_at - self._clock()).total_seconds()),
                ),
            )
        except TimeoutError as exc:
            raise MCPTimeoutError("MCP tool invocation timed out") from exc
        completed_at = self._clock()
        if result.isError:
            return MCPInvocationResult(
                invocation_id=invocation.invocation_id,
                status=MCPInvocationStatus.BUSINESS_FAILURE,
                error=MCPErrorDetail(
                    error_code="MCP_REMOTE_TOOL_ERROR",
                    message=_safe_result_message(result.content),
                    recoverable=False,
                ),
                started_at=started_at,
                completed_at=completed_at,
            )
        output = _normalize_tool_result(result)
        return MCPInvocationResult(
            invocation_id=invocation.invocation_id,
            status=MCPInvocationStatus.SUCCESS,
            output=output,
            started_at=started_at,
            completed_at=completed_at,
        )

    async def _read_resource(
        self, handle: _SDKSessionHandle, capability: MCPResourceCapability
    ) -> tuple[MCPResourceContent, ...]:
        uri = handle.resource_uris.get(capability.name)
        if uri is None:
            raise MCPInvocationError("MCP resource is not present in this session")
        result = await asyncio.wait_for(
            handle.session.read_resource(AnyUrl(uri)), timeout=self._invocation_timeout
        )
        contents: list[MCPResourceContent] = []
        for item in result.contents:
            if isinstance(item, types.TextResourceContents):
                contents.append(
                    MCPResourceContent(str(item.uri), item.mimeType, text=item.text[:1_000_000])
                )
            else:
                contents.append(
                    MCPResourceContent(
                        str(item.uri), item.mimeType, blob_base64=item.blob[:1_000_000]
                    )
                )
        return tuple(contents)

    async def _get_prompt(
        self,
        handle: _SDKSessionHandle,
        capability: MCPPromptCapability,
        arguments: JsonObject,
    ) -> MCPPromptResult:
        external_name = handle.prompt_names.get(capability.name)
        if external_name is None:
            raise MCPInvocationError("MCP prompt is not present in this session")
        result = await asyncio.wait_for(
            handle.session.get_prompt(
                external_name,
                {key: str(value) for key, value in arguments.root.items()},
            ),
            timeout=self._invocation_timeout,
        )
        return MCPPromptResult(
            description=result.description,
            messages=tuple(
                JsonObject(cast(dict[str, JsonValue], message.model_dump(mode="json")))
                for message in result.messages
            ),
        )

    def _sdk_sampling_callback(self) -> Callable[..., Any]:
        callback = self._sampling_callback
        if callback is None:  # pragma: no cover
            raise MCPProtocolError("Sampling callback is unavailable")

        async def adapter(_context: object, params: types.CreateMessageRequestParams) -> object:
            request = JsonObject(cast(dict[str, JsonValue], params.model_dump(mode="json")))
            response = await asyncio.to_thread(callback, request)
            return types.CreateMessageResult.model_validate(response.root)

        return adapter

    def _sdk_elicitation_callback(self) -> Callable[..., Any]:
        callback = self._elicitation_callback
        if callback is None:  # pragma: no cover
            raise MCPProtocolError("Elicitation callback is unavailable")

        async def adapter(_context: object, params: types.ElicitRequestParams) -> object:
            request = JsonObject(cast(dict[str, JsonValue], params.model_dump(mode="json")))
            response = await asyncio.to_thread(callback, request)
            return types.ElicitResult.model_validate(response.root)

        return adapter

    def _sdk_roots_callback(self) -> Callable[..., Any]:
        callback = self._roots_callback
        if callback is None:  # pragma: no cover
            raise MCPProtocolError("Roots callback is unavailable")

        async def adapter(_context: object) -> types.ListRootsResult:
            roots = await asyncio.to_thread(callback)
            return types.ListRootsResult(
                roots=[types.Root(uri=cast(Any, uri), name=name) for uri, name in roots]
            )

        return adapter

    def _sdk_message_handler(self) -> Callable[..., Any]:
        callback = self._notification_callback
        if callback is None:  # pragma: no cover
            raise MCPProtocolError("MCP notification callback is unavailable")

        async def adapter(message: object) -> None:
            event: str | None = None
            if isinstance(message, types.ServerNotification):
                notification = message.root
                if isinstance(notification, types.ToolListChangedNotification):
                    event = "tools/list_changed"
                elif isinstance(notification, types.ResourceListChangedNotification):
                    event = "resources/list_changed"
                elif isinstance(notification, types.PromptListChangedNotification):
                    event = "prompts/list_changed"
            if event is not None:
                await asyncio.to_thread(callback, event)

        return adapter

    def _require_handle(self) -> _SDKSessionHandle:
        if self._handle is None:
            raise MCPProtocolError("MCP session is not connected and negotiated")
        return self._handle


class _SDKTokenVerifier:
    """Adapt stable verified claims to the SDK's bearer-auth contract."""

    def __init__(self, verifier: MCPTokenVerifierPort) -> None:
        self._verifier = verifier

    async def verify_token(self, token: str) -> AccessToken | None:
        verified = await asyncio.to_thread(self._verifier.verify, token)
        if verified is None:
            return None
        identity = verified.identity
        claims = {
            "iss": identity.issuer,
            "aud": identity.audience,
            "tenant_id": identity.tenant_id,
            "user_id": identity.user_id,
            "roles": list(identity.roles),
            "data_scope": list(identity.data_scope),
            "purpose": identity.purpose,
            "authentication_source": identity.authentication_source,
            "token_fingerprint": verified.token_fingerprint,
        }
        return AccessToken(
            token="[BOUND-AT-ADAPTER]",
            client_id=identity.client_id,
            scopes=list(identity.scopes),
            expires_at=int(identity.expires_at.timestamp()) if identity.expires_at else None,
            resource=identity.audience,
            subject=identity.subject,
            claims=claims,
        )


class MCPProtocolServer:
    """Real 2025-11-25 server delegating every capability to stable providers."""

    def __init__(
        self,
        dispatch: MCPServerDispatch,
        *,
        name: str = "agentic-enterprise-knowledge-copilot",
        version: str = "0.1.0",
        token_verifier: MCPTokenVerifierPort | None = None,
        required_http_scopes: tuple[str, ...] = ("mcp.tools.read",),
        stdio_identity: MCPClientIdentity | None = None,
    ) -> None:
        self._dispatch = dispatch
        self._token_verifier = token_verifier
        self._required_http_scopes = required_http_scopes
        self._stdio_identity = stdio_identity
        self._server: Server[dict[str, object], object] = Server(name, version=version)
        self._session_manager: StreamableHTTPSessionManager | None = None
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self._server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
        async def list_tools() -> list[types.Tool]:
            identity = self._current_identity()
            capabilities = await asyncio.to_thread(self._dispatch.list_tools, identity)
            return [
                types.Tool(
                    name=item.name,
                    title=item.title,
                    description=item.description,
                    inputSchema=dict(item.input_schema.root),
                    outputSchema=dict(item.output_schema.root),
                    annotations=types.ToolAnnotations(
                        readOnlyHint=item.read_only,
                        destructiveHint=item.destructive,
                        idempotentHint=item.idempotent,
                    ),
                )
                for item in capabilities
            ]

        @self._server.call_tool()  # type: ignore[untyped-decorator]
        async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
            identity = self._current_identity()
            metadata = self._current_metadata()
            try:
                result = await asyncio.to_thread(
                    self._dispatch.invoke_tool,
                    name,
                    JsonObject(cast(dict[str, JsonValue], arguments)),
                    identity,
                    metadata,
                )
            except MCPError as exc:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=exc.detail.error_code)],
                    isError=True,
                )
            if result.status is not MCPInvocationStatus.SUCCESS or result.output is None:
                code = result.error.error_code if result.error else "MCP_INVOCATION_FAILED"
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=code)], isError=True
                )
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=json.dumps(result.output.root, sort_keys=True, separators=(",", ":")),
                    )
                ],
                structuredContent=dict(result.output.root),
                isError=False,
            )

        @self._server.list_resources()  # type: ignore[no-untyped-call,untyped-decorator]
        async def list_resources() -> list[types.Resource]:
            identity = self._current_identity()
            resources = await asyncio.to_thread(self._dispatch.list_resources, identity)
            return [
                types.Resource(
                    uri=cast(Any, item.uri),
                    name=item.name,
                    title=item.title,
                    description=item.description,
                    mimeType=item.mime_type,
                )
                for item in resources
            ]

        @self._server.read_resource()  # type: ignore[no-untyped-call,untyped-decorator]
        async def read_resource(uri: AnyUrl) -> str:
            identity = self._current_identity()
            content = await asyncio.to_thread(self._dispatch.read_resource, str(uri), identity)
            if content.text is None:
                raise MCPInvalidResponseError("Binary resources are not exported by this provider")
            return content.text

        @self._server.list_prompts()  # type: ignore[no-untyped-call,untyped-decorator]
        async def list_prompts() -> list[types.Prompt]:
            identity = self._current_identity()
            prompts = await asyncio.to_thread(self._dispatch.list_prompts, identity)
            return [
                types.Prompt(
                    name=item.name,
                    title=item.title,
                    description=item.description,
                    arguments=_sdk_prompt_arguments(item),
                )
                for item in prompts
            ]

        @self._server.get_prompt()  # type: ignore[no-untyped-call,untyped-decorator]
        async def get_prompt(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
            identity = self._current_identity()
            result = await asyncio.to_thread(
                self._dispatch.get_prompt,
                name,
                JsonObject(cast(dict[str, JsonValue], arguments or {})),
                identity,
            )
            return types.GetPromptResult.model_validate(
                {
                    "description": result.description,
                    "messages": [message.root for message in result.messages],
                }
            )

    def asgi_app(
        self,
        *,
        path: str = "/mcp",
        allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost"),
        allowed_origins: tuple[str, ...] = (),
        session_idle_timeout_seconds: float = 1800,
        max_request_body_size: int = 1_048_576,
    ) -> Starlette:
        """Build an authenticated Streamable HTTP ASGI app with DNS-rebinding controls."""
        if self._token_verifier is None:
            raise MCPAuthenticationError("HTTP MCP server requires a token verifier")
        if self._session_manager is not None:
            raise MCPProtocolError("MCP HTTP application can only be composed once")
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(allowed_hosts),
            allowed_origins=list(allowed_origins),
        )
        manager = StreamableHTTPSessionManager(
            app=self._server,
            json_response=False,
            stateless=False,
            security_settings=security,
            session_idle_timeout=session_idle_timeout_seconds,
            max_request_body_size=max_request_body_size,
        )
        self._session_manager = manager
        sdk_verifier = _SDKTokenVerifier(self._token_verifier)
        protected = RequireAuthMiddleware(manager.handle_request, list(self._required_http_scopes))

        @asynccontextmanager
        async def lifespan(_app: Starlette) -> AsyncIterator[None]:
            async with manager.run():
                yield

        return Starlette(
            routes=[Route(path, endpoint=protected)],
            middleware=[
                Middleware(AuthenticationMiddleware, backend=BearerAuthBackend(sdk_verifier))
            ],
            lifespan=lifespan,
        )

    async def run_stdio(self) -> None:
        """Run the same governed server over stdio for an approved local principal."""
        if self._stdio_identity is None:
            raise MCPAuthenticationError("stdio MCP server requires a bound local identity")
        async with stdio_server() as (read_stream, write_stream):
            await self._server.run(
                read_stream,
                write_stream,
                self._server.create_initialization_options(
                    NotificationOptions(
                        prompts_changed=False,
                        resources_changed=False,
                        tools_changed=True,
                    )
                ),
            )

    def _current_identity(self) -> MCPClientIdentity:
        try:
            request = cast(Request, self._server.request_context.request)
        except LookupError:
            request = None
        if request is not None:
            user = request.scope.get("user")
            if isinstance(user, AuthenticatedUser):
                access = user.access_token
                claims = access.claims or {}
                expires = (
                    datetime.fromtimestamp(access.expires_at, UTC)
                    if access.expires_at is not None
                    else None
                )
                return MCPClientIdentity(
                    client_id=access.client_id,
                    user_id=str(claims.get("user_id") or access.subject or access.client_id),
                    tenant_id=str(claims.get("tenant_id") or ""),
                    roles=tuple(str(item) for item in claims.get("roles", [])),
                    scopes=tuple(access.scopes),
                    data_scope=tuple(str(item) for item in claims.get("data_scope", [])),
                    purpose=str(claims.get("purpose") or "supplier_quality_analysis.v1"),
                    issuer=str(claims.get("iss")) if claims.get("iss") else None,
                    audience=access.resource,
                    subject=access.subject,
                    expires_at=expires,
                    authentication_source=str(
                        claims.get("authentication_source") or "mcp_oauth_bearer"
                    ),
                )
        if self._stdio_identity is not None:
            return self._stdio_identity
        raise MCPAuthenticationError("MCP request has no authenticated identity")

    def _current_metadata(self) -> JsonObject:
        try:
            meta = self._server.request_context.meta
        except LookupError:
            return JsonObject({})
        if meta is None:
            return JsonObject({})
        raw = meta.model_dump(mode="json", by_alias=True)
        return JsonObject(cast(dict[str, JsonValue], raw))


def _sdk_prompt_arguments(item: MCPPromptCapability) -> list[types.PromptArgument]:
    properties = item.arguments_schema.root.get("properties")
    names = tuple(properties) if isinstance(properties, dict) else ()
    required_value = item.arguments_schema.root.get("required")
    required = (
        {value for value in required_value if isinstance(value, str)}
        if isinstance(required_value, list)
        else set()
    )
    return [types.PromptArgument(name=name, required=name in required) for name in names]


def _normalize_tool_result(result: types.CallToolResult) -> JsonObject:
    if result.structuredContent is not None:
        payload: object = result.structuredContent
    else:
        payload = {
            "content": [item.model_dump(mode="json", by_alias=True) for item in result.content]
        }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > 1_048_576:
        raise MCPInvalidResponseError("MCP tool result exceeds the configured size limit")
    if not isinstance(payload, dict):  # pragma: no cover
        raise MCPInvalidResponseError("MCP tool result must normalize to an object")
    return JsonObject(cast(dict[str, JsonValue], payload))


def _safe_result_message(content: list[types.ContentBlock]) -> str:
    text = "MCP server reported a tool error"
    if content and isinstance(content[0], types.TextContent):
        text = content[0].text
    redacted = redact_for_logging(text)
    return str(redacted)[:512] if redacted else "MCP server reported a tool error"


def _translate_sdk_error(error: BaseException, *, operation: str) -> MCPError:
    """Map SDK/provider exceptions without returning their message or network details."""
    cause = error.__cause__ if isinstance(error, Exception) and error.__cause__ else error
    if isinstance(cause, asyncio.CancelledError):
        return MCPCancelledError(f"MCP {operation} was cancelled")
    if isinstance(cause, (TimeoutError, httpx.TimeoutException)):
        return MCPTimeoutError(f"MCP {operation} timed out")
    if isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code in {401, 403}:
        return MCPAuthenticationError(f"MCP {operation} authentication failed")
    if isinstance(cause, (httpx.TransportError, OSError)):
        return MCPTransportError(f"MCP {operation} transport failed")
    return MCPConnectionError(f"MCP {operation} failed")


def _safe_identifier(value: str, *, prefix: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "._:-" else "-" for character in value
    ).strip("-.")
    if not normalized:
        normalized = f"{prefix}-{hashlib.sha256(value.encode()).hexdigest()[:12]}"
    return normalized[:200]


__all__ = [
    "MCPElicitationCallback",
    "MCPNotificationCallback",
    "MCPPromptResult",
    "MCPProtocolClient",
    "MCPProtocolDiscovery",
    "MCPProtocolServer",
    "MCPResourceContent",
    "MCPRootsCallback",
    "MCPSamplingCallback",
    "MCPServerDispatch",
    "MCPTokenVerifierPort",
    "PINNED_PROTOCOL_REVISION",
    "SDK_VERSION_RANGE",
]
