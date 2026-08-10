"""Safe non-secret MCP connection configuration loading."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from copilot.contracts import MCPConnection
from copilot.mcp.errors import MCPConfigurationError

MAX_CONNECTION_FILE_BYTES = 65_536


def load_connection(path: Path) -> MCPConnection:
    """Load one bounded approved connection document containing references, never secrets."""
    resolved = path.resolve(strict=True)
    if resolved.stat().st_size > MAX_CONNECTION_FILE_BYTES:
        raise MCPConfigurationError("MCP connection configuration exceeds the size limit")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        connection = MCPConnection.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise MCPConfigurationError("MCP connection configuration is invalid") from exc
    lowered = resolved.read_text(encoding="utf-8").lower()
    if any(
        marker in lowered
        for marker in ('"access_token"', '"refresh_token"', '"password"', '"client_secret"')
    ):
        raise MCPConfigurationError("MCP connection configuration contains raw credentials")
    return connection


__all__ = ["MAX_CONNECTION_FILE_BYTES", "load_connection"]
