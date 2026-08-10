"""Run the configured governed MCP server without embedding application behavior."""

from __future__ import annotations

import argparse

import uvicorn

from copilot.bootstrap.mcp import build_mcp_container
from copilot.config import get_settings
from copilot.mcp.server.capability_exporter import MCPExportRule


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", action="append", required=True)
    args = parser.parse_args()
    settings = get_settings()
    rules = tuple(
        MCPExportRule(tool_name=name, allowed_tenants=tuple(args.tenant))
        for name in settings.mcp_export_allowlist
    )
    with build_mcp_container(settings, export_rules=rules) as container:
        server = container.protocol_server
        if server is None:
            raise RuntimeError("MCP server is disabled")
        app = server.asgi_app(
            path=settings.mcp_http_path,
            allowed_hosts=settings.mcp_allowed_hosts,
            allowed_origins=settings.mcp_allowed_origins,
        )
        uvicorn.run(
            app,
            host=settings.mcp_http_host,
            port=settings.mcp_http_port,
            log_level=settings.log_level.lower(),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
