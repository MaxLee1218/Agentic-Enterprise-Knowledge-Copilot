from __future__ import annotations

from pathlib import Path

from mcp import types

from copilot.contracts import MCPProtocolRevision
from copilot.mcp.protocol import PINNED_PROTOCOL_REVISION, SDK_VERSION_RANGE


def test_protocol_and_sdk_compatibility_are_explicitly_pinned() -> None:
    assert PINNED_PROTOCOL_REVISION is MCPProtocolRevision.V2025_11_25
    assert types.LATEST_PROTOCOL_VERSION == "2025-11-25"
    assert SDK_VERSION_RANGE == ">=1.29,<2.0"


def test_official_sdk_imports_do_not_cross_the_protocol_adapter() -> None:
    production_root = Path(__file__).resolve().parents[3] / "src" / "copilot"
    offenders: list[str] = []
    for path in production_root.rglob("*.py"):
        if path.name == "protocol.py" and path.parent.name == "mcp":
            continue
        content = path.read_text(encoding="utf-8")
        if "import mcp" in content or "from mcp" in content:
            offenders.append(str(path.relative_to(production_root)))
    assert offenders == []
