"""Runtime credential resolution without raw secret persistence."""

from __future__ import annotations

import os
import re
from typing import Protocol

from pydantic import SecretStr

from copilot.mcp.errors import MCPAuthenticationError, MCPConfigurationError

_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")


class CredentialProvider(Protocol):
    def resolve(self, reference: str | None) -> SecretStr | None: ...


class EnvCredentialProvider:
    """Development provider that resolves explicitly referenced environment secrets."""

    def __init__(self, *, allowed_names: tuple[str, ...]) -> None:
        self._allowed_names = frozenset(allowed_names)

    def resolve(self, reference: str | None) -> SecretStr | None:
        if reference is None:
            return None
        scheme, separator, name = reference.partition(":")
        if separator != ":" or scheme != "env" or not _ENV_NAME.fullmatch(name):
            raise MCPConfigurationError("Credential reference is not supported")
        if name not in self._allowed_names:
            raise MCPAuthenticationError("Credential reference is not approved")
        value = os.environ.get(name)
        if value is None or not value.strip():
            raise MCPAuthenticationError("Referenced MCP credential is unavailable")
        return SecretStr(value)


__all__ = ["CredentialProvider", "EnvCredentialProvider"]
