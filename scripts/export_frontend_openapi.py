"""Export the deterministic public FastAPI schema consumed by the frontend."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def main(argv: Sequence[str] | None = None) -> int:
    """Write the normalized OpenAPI document to the requested repository path."""
    from copilot.api.app import create_app

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    target = arguments.output.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"
    target.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
