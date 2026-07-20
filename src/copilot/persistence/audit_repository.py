"""Append-only persistence boundary for tool execution audit records."""

from threading import RLock

from copilot.tools.base import ToolAuditRecord


class InMemoryToolAuditRepository:
    """Thread-safe local audit repository with one record per invocation attempt."""

    def __init__(self) -> None:
        self._records: list[ToolAuditRecord] = []
        self._call_ids: set[str] = set()
        self._lock = RLock()

    def append(self, record: ToolAuditRecord) -> None:
        """Append one record and reject attempts to rewrite an existing call audit."""
        with self._lock:
            if record.tool_call_id in self._call_ids:
                raise ValueError("tool call audit record already exists")
            self._records.append(record)
            self._call_ids.add(record.tool_call_id)

    def list(self) -> tuple[ToolAuditRecord, ...]:
        """Return an immutable snapshot in append order."""
        with self._lock:
            return tuple(self._records)
