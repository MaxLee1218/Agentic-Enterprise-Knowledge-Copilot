"""Thin CLI for the deterministic offline Agent evaluation system."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation.baseline import baseline_from_run, write_baseline
from evaluation.config import DEFAULT_DATASET, EvaluationConfig
from evaluation.dataset_loader import DatasetValidationError, load_dataset
from evaluation.reporting import write_reports
from evaluation.runner import EvaluationRunner

EXIT_SUCCESS = 0
EXIT_USAGE_OR_DATASET = 2
EXIT_INTERNAL = 3
EXIT_GATE = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run reproducible offline Agent evaluations through the production Task Service."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--mode", choices=("mock", "live"), default="mock")
    parser.add_argument("--case", dest="case_ids", action="append", default=[])
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation/reports"))
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument("--write-baseline", type=Path)
    parser.add_argument("--update-latest", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Compatibility alias for the legacy evaluation plumbing smoke test.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Legacy --smoke JSON output path; use --output-dir for Agent evaluation.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.smoke:
        from evaluation.smoke_eval import run_smoke_evaluation

        output = arguments.output or Path("evaluation/reports/smoke-report.json")
        run_smoke_evaluation(output)
        print("Evaluation smoke test passed")
        return EXIT_SUCCESS
    if arguments.mode == "live":
        print(
            "Live evaluation is intentionally unavailable in Stage 14; use --mode mock.",
            file=sys.stderr,
        )
        return EXIT_USAGE_OR_DATASET
    if arguments.max_workers != 1:
        print("Only deterministic --max-workers 1 is currently supported.", file=sys.stderr)
        return EXIT_USAGE_OR_DATASET
    try:
        dataset = load_dataset(
            arguments.dataset,
            case_ids=tuple(arguments.case_ids),
            tags=tuple(arguments.tag),
        )
        if arguments.write_baseline and (arguments.case_ids or arguments.tag):
            raise DatasetValidationError("A baseline must be created from the complete dataset")
        config = EvaluationConfig(
            mode=arguments.mode,
            seed=arguments.seed,
            max_workers=arguments.max_workers,
            timeout_seconds=arguments.timeout_seconds,
            output_dir=arguments.output_dir,
            update_latest=arguments.update_latest,
        )
        run = EvaluationRunner(config).run(dataset, baseline_path=arguments.baseline)
        run_directory = write_reports(
            run,
            {case.case_id: case for case in dataset.cases},
            arguments.output_dir,
            update_latest=arguments.update_latest,
        )
        if arguments.write_baseline:
            if run.errored_cases:
                print("Baseline was not written because cases errored.", file=sys.stderr)
                return EXIT_INTERNAL
            write_baseline(baseline_from_run(run), arguments.write_baseline)
        print(
            f"Evaluation complete: {run.passed_cases}/{run.total_cases} cases passed; "
            f"report={run_directory}"
        )
        if arguments.verbose:
            for result in run.case_results:
                print(f"{result.case_id}: {result.status.value}")
        if run.errored_cases:
            return EXIT_INTERNAL
        if arguments.fail_on_regression and not run.gate_result.passed:
            return EXIT_GATE
        return EXIT_SUCCESS
    except DatasetValidationError as exc:
        print(f"Dataset error: {exc}", file=sys.stderr)
        return EXIT_USAGE_OR_DATASET
    except (OSError, ValueError) as exc:
        print(f"Evaluation error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
