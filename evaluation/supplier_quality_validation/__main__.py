"""Verify the versioned bundle; intentionally no product-execution mode."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from evaluation.supplier_quality_validation import DEFAULT_BUNDLE, verify_bundle


def main(argv: Sequence[str] | None = None) -> int:
    """Return nonzero on invalid or changed inputs without rewriting any file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    arguments = parser.parse_args(argv)
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
