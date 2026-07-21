"""Command-line entry point for offline project evaluations."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

DEFAULT_SMOKE_REPORT = Path("evaluation/reports/smoke-report.json")


def build_parser() -> argparse.ArgumentParser:
    """Build the evaluation command-line parser."""
    parser = argparse.ArgumentParser(
        description="Run deterministic Agentic Enterprise Knowledge Copilot evaluations."
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run the offline dataset, metric, and report-generation smoke pipeline.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_SMOKE_REPORT,
        help="Path for the generated JSON evaluation report.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the selected evaluation and return a process exit code."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if not arguments.smoke:
        parser.error("no evaluation selected; pass --smoke")

    if __package__ in {None, ""}:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from evaluation.smoke_eval import run_smoke_evaluation

    run_smoke_evaluation(arguments.output)
    print("Evaluation smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
