"""Thin, offline-only CLI for the P01 evaluation slice."""

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from evaluation.supplier_quality_pilot.runner import run_p01
from evaluation.supplier_quality_pilot.source import DEFAULT_SOURCE


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 for qualified P01, 1 for failed acceptance, 2 for input, 3 for harness error."""
    parser = argparse.ArgumentParser(description="Evaluate offline Supplier Quality P01")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    arguments = parser.parse_args(argv)
    destination = arguments.output_dir or (
        Path("evaluation/reports/supplier-quality-pilot")
        / f"{datetime.now(UTC):%Y%m%dT%H%M%S.%fZ}-{uuid4().hex[:8]}"
    )
    try:
        run = run_p01(destination, source_path=arguments.source)
    except (ValueError, OSError) as exc:
        print(f"Pilot input/output error: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(
        f"P01 qualified={run.qualified_delivery}; "
        f"numeric={run.numeric_passed}/{run.numeric_total}; "
        f"report={destination.resolve() / 'report.json'}"
    )
    if run.error_code is not None and run.error_code != "WORKFLOW_INTERRUPTED":
        return 3
    return 0 if run.qualified_delivery else 1


if __name__ == "__main__":
    raise SystemExit(main())
