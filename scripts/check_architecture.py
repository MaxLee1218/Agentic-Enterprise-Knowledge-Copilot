"""Enforce package dependency boundaries with Python AST import analysis."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
SOURCE_ROOT: Final = PROJECT_ROOT / "src" / "copilot"


class Layer(StrEnum):
    """Conceptual architecture layers mapped onto the current package layout."""

    DOMAIN = "domain"
    APPLICATION = "application"
    INFRASTRUCTURE = "infrastructure"
    INTERFACES = "interfaces"
    BOOTSTRAP = "bootstrap"
    SHARED = "shared"


@dataclass(frozen=True, slots=True)
class ImportReference:
    """One imported module discovered in a source file."""

    module: str
    line: int


@dataclass(frozen=True, slots=True)
class ArchitectureViolation:
    """A forbidden source dependency with enough context for remediation."""

    path: Path
    line: int
    source_layer: Layer
    imported_module: str
    reason: str

    def render(self, project_root: Path = PROJECT_ROOT) -> str:
        """Render a stable, human-readable CI error."""
        try:
            display_path = self.path.relative_to(project_root)
        except ValueError:
            display_path = self.path
        return (
            f"{display_path}:{self.line}: {self.source_layer} imports "
            f"{self.imported_module}: {self.reason}"
        )


INTERNAL_ALLOWED_LAYERS: Final = {
    Layer.DOMAIN: frozenset({Layer.DOMAIN, Layer.SHARED}),
    Layer.APPLICATION: frozenset({Layer.DOMAIN, Layer.APPLICATION, Layer.SHARED}),
    Layer.INFRASTRUCTURE: frozenset(
        {Layer.DOMAIN, Layer.APPLICATION, Layer.INFRASTRUCTURE, Layer.SHARED}
    ),
    Layer.INTERFACES: frozenset({Layer.DOMAIN, Layer.APPLICATION, Layer.INTERFACES, Layer.SHARED}),
    Layer.BOOTSTRAP: frozenset(Layer),
    Layer.SHARED: frozenset({Layer.DOMAIN, Layer.SHARED}),
}
FORBIDDEN_DOMAIN_EXTERNALS: Final = frozenset(
    {
        "anthropic",
        "chromadb",
        "fastapi",
        "mcp",
        "openai",
        "psycopg",
        "qdrant_client",
        "sqlalchemy",
        "typer",
        "uvicorn",
    }
)
FORBIDDEN_APPLICATION_EXTERNALS: Final = FORBIDDEN_DOMAIN_EXTERNALS
GENERIC_TOOL_MODULES: Final = frozenset(
    {"base", "exceptions", "executor", "registry", "runner", "schema"}
)
CONCRETE_TOOL_PREFIXES: Final = tuple(
    f"copilot.tools.{capability}"
    for capability in ("analytics", "database", "knowledge", "reporting")
)


def classify_path(path: Path, source_root: Path = SOURCE_ROOT) -> Layer:
    """Map one Python source path to its conceptual architecture layer."""
    relative = path.relative_to(source_root)
    first = relative.parts[0]
    if first == "contracts":
        return Layer.DOMAIN
    if first in {"agent", "policies", "services"}:
        return Layer.APPLICATION
    if first == "tools":
        if len(relative.parts) == 2 and relative.stem in GENERIC_TOOL_MODULES | {"__init__"}:
            return Layer.APPLICATION
        return Layer.INFRASTRUCTURE
    if first in {"evidence", "llm", "mcp", "observability", "persistence"}:
        return Layer.INFRASTRUCTURE
    if first in {"api", "cli"}:
        return Layer.INTERFACES
    if first == "bootstrap":
        return Layer.BOOTSTRAP
    return Layer.SHARED


def classify_module(module: str) -> Layer | None:
    """Map an absolute internal import to a conceptual architecture layer."""
    parts = module.split(".")
    if not parts or parts[0] != "copilot":
        return None
    if len(parts) == 1:
        return Layer.SHARED
    first = parts[1]
    if first == "contracts":
        return Layer.DOMAIN
    if first in {"agent", "policies", "services"}:
        return Layer.APPLICATION
    if first == "tools":
        if len(parts) == 2 or parts[2] in GENERIC_TOOL_MODULES:
            return Layer.APPLICATION
        return Layer.INFRASTRUCTURE
    if first in {"evidence", "llm", "mcp", "observability", "persistence"}:
        return Layer.INFRASTRUCTURE
    if first in {"api", "cli"}:
        return Layer.INTERFACES
    if first == "bootstrap":
        return Layer.BOOTSTRAP
    return Layer.SHARED


def _module_package(path: Path, source_root: Path) -> tuple[str, ...]:
    relative = path.relative_to(source_root).with_suffix("")
    return ("copilot", *relative.parts[:-1])


def _resolve_import_from(node: ast.ImportFrom, path: Path, source_root: Path) -> str:
    if node.level == 0:
        return node.module or ""
    package = _module_package(path, source_root)
    keep = len(package) - (node.level - 1)
    prefix = package[: max(keep, 0)]
    suffix = tuple((node.module or "").split(".")) if node.module else ()
    return ".".join((*prefix, *suffix))


def find_imports(path: Path, source_root: Path = SOURCE_ROOT) -> tuple[ImportReference, ...]:
    """Parse imports from one Python file without executing it."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    references: list[ImportReference] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            references.extend(ImportReference(alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from(node, path, source_root)
            if module:
                references.append(ImportReference(module, node.lineno))
    return tuple(references)


def check_architecture(source_root: Path = SOURCE_ROOT) -> list[ArchitectureViolation]:
    """Return every forbidden dependency under the production package."""
    violations: list[ArchitectureViolation] = []
    for path in sorted(source_root.rglob("*.py")):
        source_layer = classify_path(path, source_root)
        for reference in find_imports(path, source_root):
            relative = path.relative_to(source_root)
            if relative.parts[0] == "mcp" and reference.module.startswith(CONCRETE_TOOL_PREFIXES):
                violations.append(
                    ArchitectureViolation(
                        path=path,
                        line=reference.line,
                        source_layer=source_layer,
                        imported_module=reference.module,
                        reason="MCP code must not call concrete business tool adapters directly",
                    )
                )
                continue

            imported_layer = classify_module(reference.module)
            if imported_layer is not None:
                if imported_layer not in INTERNAL_ALLOWED_LAYERS[source_layer]:
                    violations.append(
                        ArchitectureViolation(
                            path=path,
                            line=reference.line,
                            source_layer=source_layer,
                            imported_module=reference.module,
                            reason=f"dependency on {imported_layer} is forbidden",
                        )
                    )
                continue

            external_root = reference.module.partition(".")[0]
            forbidden = (
                FORBIDDEN_DOMAIN_EXTERNALS
                if source_layer is Layer.DOMAIN
                else FORBIDDEN_APPLICATION_EXTERNALS
                if source_layer is Layer.APPLICATION
                else frozenset()
            )
            if external_root in forbidden:
                violations.append(
                    ArchitectureViolation(
                        path=path,
                        line=reference.line,
                        source_layer=source_layer,
                        imported_module=reference.module,
                        reason="concrete framework or provider dependency is forbidden",
                    )
                )

            if external_root == "mcp" and relative.as_posix() != "mcp/protocol.py":
                violations.append(
                    ArchitectureViolation(
                        path=path,
                        line=reference.line,
                        source_layer=source_layer,
                        imported_module=reference.module,
                        reason="MCP SDK imports are restricted to copilot.mcp.protocol",
                    )
                )
    return violations


def main() -> int:
    """Run dependency checks and return a CI-friendly exit code."""
    print("Architecture dependency check")
    violations = check_architecture()
    if violations:
        print("Failed:")
        for violation in violations:
            print(f"- {violation.render()}")
        return 1

    print("Passed: domain, application, infrastructure, interfaces, and bootstrap boundaries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
