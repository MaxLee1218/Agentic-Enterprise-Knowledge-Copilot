"""One-command offline P01 report and independent score."""

import json
import subprocess
import sys
from pathlib import Path


def test_offline_pilot_cli_delivers_report_and_score(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "evaluation.supplier_quality_pilot", "--output-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["qualified_delivery"] is True
    assert report["numeric_passed"] == report["numeric_total"] == 30
    assert Path(report["artifact_paths"][0]).is_file()


def test_cli_rejects_overwrite_and_invalid_source_without_creating_a_report(tmp_path: Path) -> None:
    preserved = tmp_path / "preserved.txt"
    preserved.write_text("original")
    command = [sys.executable, "-m", "evaluation.supplier_quality_pilot"]

    overwrite = subprocess.run(
        [*command, "--output-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    invalid = subprocess.run(
        [
            *command,
            "--output-dir",
            str(tmp_path / "output"),
            "--source",
            str(tmp_path / "absent.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert overwrite.returncode == invalid.returncode == 2
    assert preserved.read_text() == "original"
    assert not (tmp_path / "report.json").exists()
    assert not (tmp_path / "output").exists()
