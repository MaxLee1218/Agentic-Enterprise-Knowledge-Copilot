"""Validate required architecture documentation and ADR governance metadata."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
ADR_NAME_PATTERN: Final = re.compile(r"ADR-\d{3}-[a-z0-9]+(?:-[a-z0-9]+)*\.md")
ADR_TITLE_PATTERN: Final = re.compile(r"# ADR-\d{3}: .+")
REQUIRED_ADR_HEADINGS: Final = (
    "## Status",
    "## Context",
    "## Decision",
    "## Consequences",
)
ALLOWED_ADR_STATUSES: Final = frozenset({"Proposed", "Accepted", "Deprecated", "Superseded"})
REQUIRED_ARCHITECTURE_TERMS: Final = (
    "dependency matrix",
    "composition root",
    "transaction boundary",
    "calling direction",
    "layer boundary",
)


def _heading_value(content: str, heading: str) -> str | None:
    lines = content.splitlines()
    try:
        heading_index = lines.index(heading)
    except ValueError:
        return None

    for line in lines[heading_index + 1 :]:
        value = line.strip()
        if value.startswith("#"):
            return None
        if value:
            return value
    return None


def validate_adr(path: Path) -> list[str]:
    """Return actionable validation errors for one ADR file."""
    errors: list[str] = []
    if ADR_NAME_PATTERN.fullmatch(path.name) is None:
        errors.append(f"{path}: ADR filename must match {ADR_NAME_PATTERN.pattern}")

    content = path.read_text(encoding="utf-8")
    first_line = content.splitlines()[0] if content else ""
    if ADR_TITLE_PATTERN.fullmatch(first_line) is None:
        errors.append(f"{path}: first line must match '# ADR-XXX: Title'")

    for heading in REQUIRED_ADR_HEADINGS:
        if heading not in content.splitlines():
            errors.append(f"{path}: missing required heading '{heading}'")

    status = _heading_value(content, "## Status")
    if status not in ALLOWED_ADR_STATUSES:
        allowed = ", ".join(sorted(ALLOWED_ADR_STATUSES))
        errors.append(f"{path}: status must be one of: {allowed}")
    return errors


def check_documentation(project_root: Path = PROJECT_ROOT) -> list[str]:
    """Validate required files, architecture vocabulary, and every numbered ADR."""
    errors: list[str] = []
    required_paths = (
        project_root / "README.md",
        project_root / "docs" / "architecture.md",
        project_root / "docs" / "adr",
        project_root / "docs" / "adr" / "README.md",
    )
    for path in required_paths:
        if not path.exists():
            errors.append(f"Missing required documentation path: {path}")

    architecture_path = project_root / "docs" / "architecture.md"
    if architecture_path.is_file():
        architecture = architecture_path.read_text(encoding="utf-8").casefold()
        for term in REQUIRED_ARCHITECTURE_TERMS:
            if term.casefold() not in architecture:
                errors.append(f"{architecture_path}: missing required architecture term '{term}'")

    adr_directory = project_root / "docs" / "adr"
    if adr_directory.is_dir():
        adr_files = sorted(adr_directory.glob("ADR-*.md"))
        if not adr_files:
            errors.append(f"{adr_directory}: at least one numbered ADR is required")
        for adr_path in adr_files:
            errors.extend(validate_adr(adr_path))
    return errors


def main() -> int:
    """Run documentation checks and return a CI-friendly exit code."""
    errors = check_documentation()
    if errors:
        print("Documentation check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Documentation check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
