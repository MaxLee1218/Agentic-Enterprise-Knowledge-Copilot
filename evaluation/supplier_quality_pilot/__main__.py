"""Thin, offline-only CLI for the P01 evaluation slice."""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from evaluation.supplier_quality_pilot.budget import BudgetPolicy, BudgetStopped, PricingCard
from evaluation.supplier_quality_pilot.live import run_live_p01
from evaluation.supplier_quality_pilot.runner import run_p01
from evaluation.supplier_quality_pilot.source import DEFAULT_SOURCE


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 for qualified P01, 1 for failed acceptance, 2 for input, 3 for harness error."""
    parser = argparse.ArgumentParser(description="Evaluate offline Supplier Quality P01")
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
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("output must be empty")
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
                    "paid_attempts": 0,
                    "qualified_delivery": False,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                indent=2,
            )
        )
        print(f"P01 blocked: {exc.reason}; zero paid attempts in this invocation", file=sys.stderr)
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
