"""Thin CLI for guarded live and offline Supplier Quality pilot evaluation."""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from evaluation.supplier_quality_pilot.budget import BudgetPolicy, BudgetStopped, PricingCard
from evaluation.supplier_quality_pilot.live import run_live_p01
from evaluation.supplier_quality_pilot.runner import run_p01
from evaluation.supplier_quality_pilot.source import DEFAULT_SOURCE
from evaluation.supplier_quality_pilot.suite import run_suite


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 for acceptance, 1 for failures, 2 for input, 3 for case error, 4 for stop."""
    parser = argparse.ArgumentParser(description="Evaluate the controlled Supplier Quality pilot")
    parser.add_argument("--suite", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--calibration-dir", type=Path)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    parser.add_argument("--pricing", type=Path)
    parser.add_argument("--budget", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    arguments = parser.parse_args(argv)
    destination = arguments.output_dir or (
        Path("evaluation/reports/supplier-quality-pilot")
        / f"{datetime.now(UTC):%Y%m%dT%H%M%S.%fZ}-{uuid4().hex[:8]}"
    )
    try:
        if destination.exists() and any(destination.iterdir()) and not arguments.resume:
            raise ValueError("output must be empty")
        if arguments.suite:
            suite = run_suite(
                destination,
                mode=arguments.mode,
                source_path=arguments.source,
                pricing=PricingCard.model_validate_json(arguments.pricing.read_bytes())
                if arguments.pricing
                else None,
                policy=BudgetPolicy.model_validate_json(arguments.budget.read_bytes())
                if arguments.budget
                else None,
                calibration_directory=arguments.calibration_dir,
                baseline_report_path=arguments.baseline_report,
                resume=arguments.resume,
                persistence_url=os.environ.get("PILOT_PERSISTENCE_DATABASE_URL"),
            )
            if arguments.baseline_report:
                from evaluation.supplier_quality_pilot.comparison import publish_comparison

                publish_comparison(
                    arguments.baseline_report, destination / "suite.json", destination
                )
            print(
                f"Pilot suite qualified={suite.qualified_deliveries}/9; "
                f"numeric={suite.numeric_passed}/{suite.numeric_total}; "
                f"P10={int(suite.safety_passed)}/1; partial={suite.partial}; "
                f"report={destination.resolve() / 'suite.json'}"
            )
            if suite.stop_reason:
                return 4
            return (
                0
                if suite.qualified_deliveries == 9 and suite.safety_passed and not suite.partial
                else 1
            )
        if arguments.resume or arguments.calibration_dir or arguments.baseline_report:
            raise ValueError("suite options require --suite")
        if arguments.mode == "live":
            if arguments.pricing is None or arguments.budget is None:
                raise BudgetStopped("PRICING_AND_BUDGET_REQUIRED")
            pricing = PricingCard.model_validate_json(arguments.pricing.read_bytes())
            policy = BudgetPolicy.model_validate_json(arguments.budget.read_bytes())
            run = run_live_p01(
                destination, source_path=arguments.source, pricing=pricing, policy=policy
            )
        else:
            if arguments.pricing or arguments.budget:
                raise ValueError("live configuration requires --mode live")
            run = run_p01(destination, source_path=arguments.source)
    except BudgetStopped as exc:
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "blocked.json").write_text(
            json.dumps(
                {
                    "schema_version": "pilot-preflight-block.v1",
                    "mode": "live",
                    "case_id": "P01",
                    "reason": exc.reason,
                    "paid_attempts": None if arguments.suite else 0,
                    "qualified_delivery": False,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                indent=2,
            )
        )
        print(f"Pilot blocked: {exc.reason}; see the retained experiment ledger", file=sys.stderr)
        return 4
    except (ValueError, OSError) as exc:
        print(f"Pilot input/output error: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(
        f"P01 qualified={run.qualified_delivery}; "
        f"numeric={run.numeric_passed}/{run.numeric_total}; "
        f"report={destination.resolve() / 'report.json'}"
    )
    if run.spending and run.spending.stopped_reason:
        return 4
    if run.error_code is not None and run.error_code != "WORKFLOW_INTERRUPTED":
        return 3
    return 0 if run.qualified_delivery else 1


if __name__ == "__main__":
    raise SystemExit(main())
