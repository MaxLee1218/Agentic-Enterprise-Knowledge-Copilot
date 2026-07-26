"""CLI smoke coverage for the governed analytics flow."""

import os
import subprocess
import sys
from pathlib import Path


def test_smoke_analytics_script() -> None:
    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_root / "src")

    result = subprocess.run(
        [sys.executable, "scripts/smoke_analytics.py"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    for field in ("operation=", "formula=", "result=", "evidence_id=", "latency_ms="):
        assert field in result.stdout
