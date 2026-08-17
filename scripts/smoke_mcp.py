"""Run a fail-fast real MCP initialization and discovery smoke check."""

from __future__ import annotations

import argparse
from pathlib import Path

from copilot.mcp.config import load_connection
from copilot.mcp.protocol import PINNED_PROTOCOL_REVISION, MCPProtocolClient
from copilot.mcp.security.credential_provider import EnvCredentialProvider


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("connection", type=Path)
    parser.add_argument(
        "--credential-env",
        action="append",
        default=[],
        metavar="NAME",
        help="Allow NAME as an environment-backed credential reference (repeatable).",
    )
    args = parser.parse_args()
    connection = load_connection(args.connection)
    credential = EnvCredentialProvider(allowed_names=tuple(args.credential_env)).resolve(
        connection.credential_reference
    )
    client = MCPProtocolClient(connection)
    try:
        discovery = client.connect(
            credential=credential.get_secret_value() if credential is not None else None
        )
        if discovery.negotiated.protocol_revision is not PINNED_PROTOCOL_REVISION:
            return 1
        if not discovery.negotiated.server_capabilities:
            return 1
        print(
            f"MCP smoke passed: {discovery.server_id}, "
            f"{len(discovery.negotiated.capabilities)} capabilities"
        )
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
