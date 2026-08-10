"""Initialize and inspect one approved non-secret MCP connection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from copilot.config import get_settings
from copilot.mcp.config import load_connection
from copilot.mcp.protocol import MCPProtocolClient
from copilot.mcp.security.credential_provider import EnvCredentialProvider


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("connection", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    connection = load_connection(args.connection)
    credential = EnvCredentialProvider(allowed_names=settings.mcp_env_credential_names).resolve(
        connection.credential_reference
    )
    client = MCPProtocolClient(
        connection,
        connect_timeout_seconds=settings.mcp_connect_timeout_seconds,
        initialize_timeout_seconds=settings.mcp_initialize_timeout_seconds,
        invocation_timeout_seconds=settings.mcp_invocation_timeout_seconds,
    )
    try:
        discovery = client.connect(
            credential=credential.get_secret_value() if credential is not None else None
        )
        print(
            json.dumps(
                {
                    "server_id": discovery.server_id,
                    "server_version": discovery.server_version,
                    "protocol_revision": discovery.negotiated.protocol_revision.value,
                    "session_id": discovery.session_id,
                    "capabilities": [
                        {
                            "type": item.capability_type.value,
                            "canonical_name": item.canonical_name,
                            "schema_digest": item.provenance.schema_digest,
                        }
                        for item in discovery.negotiated.capabilities
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
