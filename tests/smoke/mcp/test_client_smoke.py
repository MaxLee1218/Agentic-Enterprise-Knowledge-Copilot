from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.mcp_helpers import PROJECT_ROOT, stdio_connection


def test_client_smoke_script_uses_real_protocol(tmp_path: Path) -> None:
    connection_path = tmp_path / "connection.json"
    connection_path.write_text(stdio_connection().model_dump_json(indent=2), encoding="utf-8")
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    source_path = str(PROJECT_ROOT / "src")
    env["PYTHONPATH"] = os.pathsep.join(
        path for path in (source_path, env.get("PYTHONPATH")) if path
    )
    completed = subprocess.run(
        [sys.executable, "scripts/smoke_mcp.py", str(connection_path)],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "MCP smoke passed" in completed.stdout
    assert "10 capabilities" in completed.stdout
