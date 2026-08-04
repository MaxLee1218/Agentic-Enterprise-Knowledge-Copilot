"""File-based Stage 14 evaluation CLI smoke test."""

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_agent_evaluation_cli_smoke_tag_writes_latest_reports(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "evaluation/run_eval.py",
            "--tag",
            "smoke",
            "--output-dir",
            str(tmp_path),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert (tmp_path / "latest.md").is_file()
    names = {metric["metric_name"] for metric in payload["metrics"]}
    assert {
        "overall_task_success_rate",
        "final_plan_validity",
        "numeric_accuracy",
        "safety_violation_rate",
        "total_tokens",
    }.issubset(names)
