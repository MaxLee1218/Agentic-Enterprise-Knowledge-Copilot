"""Canonical Streamable HTTP endpoint and DNS-rebinding validation."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Collection
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from copilot.mcp.errors import MCPOriginRejectedError

Resolver = Callable[[str, int], Collection[str]]


@dataclass(frozen=True, slots=True)
class ValidatedOrigin:
    canonical_endpoint: str
    host: str
    port: int
    resolved_addresses: tuple[str, ...]


def _system_resolver(host: str, port: int) -> Collection[str]:
    return {
        str(item[4][0])
        for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if item[4]
    }


class MCPOriginValidator:
    """Validate configured identity plus fresh DNS resolution, not request headers alone."""

    def __init__(
        self,
        *,
        approved_hosts: Collection[str],
        resolver: Resolver = _system_resolver,
        allow_private_https: bool = False,
    ) -> None:
        self._approved_hosts = frozenset(host.lower().rstrip(".") for host in approved_hosts)
        self._resolver = resolver
        self._allow_private_https = allow_private_https

    def validate(
        self,
        endpoint: str,
        *,
        expected_addresses: Collection[str] = (),
    ) -> ValidatedOrigin:
        parts = urlsplit(endpoint)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise MCPOriginRejectedError("MCP endpoint must be an HTTP(S) URL")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise MCPOriginRejectedError(
                "MCP endpoint cannot contain credentials, query, or fragment"
            )
        host = parts.hostname.lower().rstrip(".")
        if host not in self._approved_hosts:
            raise MCPOriginRejectedError("MCP endpoint host is not approved")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            addresses = tuple(
                sorted({str(ipaddress.ip_address(item)) for item in self._resolver(host, port)})
            )
        except (OSError, ValueError) as exc:
            raise MCPOriginRejectedError("MCP endpoint could not be resolved safely") from exc
        if not addresses:
            raise MCPOriginRejectedError("MCP endpoint has no validated address")
        parsed = tuple(ipaddress.ip_address(item) for item in addresses)
        is_loopback = all(item.is_loopback for item in parsed)
        if parts.scheme == "http" and not is_loopback:
            raise MCPOriginRejectedError("Plain HTTP MCP endpoints must resolve only to loopback")
        if (
            parts.scheme == "https"
            and not self._allow_private_https
            and any(item.is_private or item.is_link_local or item.is_unspecified for item in parsed)
        ):
            raise MCPOriginRejectedError("Private MCP endpoint resolution is not approved")
        expected = {str(ipaddress.ip_address(item)) for item in expected_addresses}
        if expected and set(addresses) != expected:
            raise MCPOriginRejectedError("MCP endpoint address changed since approval")
        path = parts.path.rstrip("/") or "/mcp"
        netloc = host if port in {80, 443} else f"{host}:{port}"
        canonical = urlunsplit((parts.scheme, netloc, path, "", ""))
        return ValidatedOrigin(canonical, host, port, addresses)


__all__ = ["MCPOriginValidator", "ValidatedOrigin"]
