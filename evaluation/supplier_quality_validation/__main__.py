"""Read-only freeze verification by default; explicit budgeted first live execution."""

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from evaluation.supplier_quality_validation import DEFAULT_BUNDLE, verify_bundle


def main(argv: Sequence[str] | None = None) -> int:
    """Return nonzero on invalid or changed inputs without rewriting any file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pricing", type=Path)
    parser.add_argument("--previous-pricing", type=Path)
    parser.add_argument("--budget", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.execute_live:
        if arguments.bundle != DEFAULT_BUNDLE or not all(
            (arguments.output, arguments.pricing, arguments.previous_pricing, arguments.budget)
        ):
            parser.error(
                "live requires the original bundle, --output, --pricing, "
                "--previous-pricing and --budget"
            )
        url = os.environ.get("VALIDATION_POSTGRES_URL")
        if not url:
            parser.error("set a dedicated migrated VALIDATION_POSTGRES_URL")
        from evaluation.supplier_quality_pilot.budget import (
            BudgetPolicy,
            BudgetStopped,
            PricingCard,
        )
        from evaluation.supplier_quality_validation.live import run_validation_live

        try:
            report = run_validation_live(
                arguments.output,
                pricing=PricingCard.model_validate_json(arguments.pricing.read_bytes()),
                previous_pricing=PricingCard.model_validate_json(
                    arguments.previous_pricing.read_bytes()
                ),
                policy=BudgetPolicy.model_validate_json(arguments.budget.read_bytes()),
                persistence_url=url,
            )
        except (BudgetStopped, ValueError, OSError) as exc:
            # Never print settings/provider/DB exceptions or raw URLs.
            reason = exc.reason if isinstance(exc, BudgetStopped) else type(exc).__name__
            print(f"Validation live stopped: {reason}")
            return 2
        print(
            f"First population: reports {report.qualified_deliveries}/{report.report_total}; "
            f"safety {report.safety_passed}/{report.safety_total}; partial={report.partial}"
        )
        return (
            2
            if report.partial
            else (0 if report.qualified_deliveries == 16 and report.safety_passed == 4 else 1)
        )

    try:
        bundle = verify_bundle(arguments.bundle)
    except (ValueError, OSError) as exc:
        print(f"Validation freeze rejected: {exc}")
        return 1
    print(
        f"Verified V01–V20: reports /16, safety /4, numeric /{bundle.numeric_total}, "
        f"facts /{bundle.fact_total}; freeze={bundle.freeze_hash}. "
        "This command performs no product Tasks or model calls; no delivery results measured."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
