"""Hermetic governed Copilot MCP server used by real OAuth interoperability tests."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from pydantic import SecretStr

from copilot.bootstrap.mcp import build_mcp_container
from copilot.config import Settings
from copilot.mcp.server.capability_exporter import MCPExportRule


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    signing_key = os.environ["MCP_TEST_SIGNING_KEY"]
    with TemporaryDirectory(prefix="copilot-mcp-oauth-") as temporary_directory:
        settings = Settings(
            app_env="test",
            database_url="sqlite:///unused.db",
            checkpoint_enabled=False,
            artifact_dir=Path(temporary_directory) / "artifacts",
            mcp_enabled=True,
            mcp_server_enabled=True,
            mcp_http_port=args.port,
            mcp_export_allowlist=("knowledge_search",),
            mcp_jwt_issuer="https://issuer.example.test",
            mcp_jwt_audience="copilot-mcp-test",
            mcp_jwt_signing_key=SecretStr(signing_key),
        )
        with build_mcp_container(
            settings,
            export_rules=(
                MCPExportRule(tool_name="knowledge_search", allowed_tenants=("tenant-alpha",)),
            ),
        ) as container:
            protocol_server = container.protocol_server
            if protocol_server is None:  # pragma: no cover
                raise RuntimeError("MCP test server composition failed")
            app = protocol_server.asgi_app(
                allowed_hosts=(f"127.0.0.1:{args.port}", f"localhost:{args.port}")
            )
            uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
