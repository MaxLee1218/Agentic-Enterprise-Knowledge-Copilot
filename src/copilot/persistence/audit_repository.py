"""Append-only persistence boundary for tool execution audit records."""

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import RLock

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from copilot.contracts import JsonObject, ToolResultStatus
from copilot.persistence.database import PersistenceDatabase, coerce_database
from copilot.persistence.models import WorkflowGraphAuditRow, WorkflowToolAuditRow
from copilot.security.redaction import redact_for_logging
from copilot.services.workflows.models import WorkflowAuditRecord
from copilot.tools.base import ToolAuditRecord


class InMemoryToolAuditRepository:
    """Thread-safe local audit repository with one record per invocation attempt."""

    def __init__(
        self,
        database_path: PersistenceDatabase | Path | None = None,
        *,
        initialize_schema: bool = True,
    ) -> None:
        self._records: list[ToolAuditRecord] = []
        self._call_ids: set[str] = set()
        self._lock = RLock()
        self._database, self._owns_database = coerce_database(
            database_path,
            initialize_schema=initialize_schema,
        )

    def append(self, record: ToolAuditRecord) -> None:
        """Append one record and reject attempts to rewrite an existing call audit."""
        with self._lock:
            if self._database is not None:
                try:
                    with self._database.session() as session:
                        session.add(
                            WorkflowToolAuditRow(
                                event_id=record.tool_call_id,
                                task_id=record.task_id,
                                payload_json=_tool_json(record),
                            )
                        )
                except IntegrityError as exc:
                    raise ValueError("tool call audit record already exists") from exc
                return
            if record.tool_call_id in self._call_ids:
                raise ValueError("tool call audit record already exists")
            self._records.append(record)
            self._call_ids.add(record.tool_call_id)

    def list(self) -> tuple[ToolAuditRecord, ...]:
        """Return an immutable snapshot in append order."""
        with self._lock:
            if self._database is not None:
                with self._database.session() as session:
                    payloads = session.scalars(
                        select(WorkflowToolAuditRow.payload_json).order_by(
                            WorkflowToolAuditRow.sequence_id
                        )
                    )
                    return tuple(_tool_record(item) for item in payloads)
            return tuple(self._records)

    def close(self) -> None:
        with self._lock:
            if self._owns_database and self._database is not None:
                self._database.dispose()
                self._database = None


class InMemoryWorkflowAuditRepository:
    """Thread-safe append-only workflow event sink."""

    def __init__(
        self,
        database_path: PersistenceDatabase | Path | None = None,
        *,
        initialize_schema: bool = True,
    ) -> None:
        self._records: list[WorkflowAuditRecord] = []
        self._event_ids: set[str] = set()
        self._lock = RLock()
        self._database, self._owns_database = coerce_database(
            database_path,
            initialize_schema=initialize_schema,
        )

    def append(self, record: WorkflowAuditRecord) -> None:
        """Append one unique structured event or fail closed."""
        safe_metadata = redact_for_logging(record.metadata.root)
        record = replace(
            record,
            metadata=JsonObject(
                safe_metadata if isinstance(safe_metadata, dict) else {"value": "[REDACTED]"}
            ),
        )
        with self._lock:
            if self._database is not None:
                try:
                    with self._database.session() as session:
                        session.add(
                            WorkflowGraphAuditRow(
                                event_id=record.event_id,
                                task_id=record.task_id,
                                payload_json=_workflow_json(record),
                            )
                        )
                except IntegrityError as exc:
                    raise ValueError("workflow audit event already exists") from exc
                return
            if record.event_id in self._event_ids:
                raise ValueError("workflow audit event already exists")
            self._records.append(record)
            self._event_ids.add(record.event_id)

    def list(self) -> tuple[WorkflowAuditRecord, ...]:
        """Return workflow events in append order."""
        with self._lock:
            if self._database is not None:
                with self._database.session() as session:
                    payloads = session.scalars(
                        select(WorkflowGraphAuditRow.payload_json).order_by(
                            WorkflowGraphAuditRow.sequence_id
                        )
                    )
                    return tuple(_workflow_record(item) for item in payloads)
            return tuple(self._records)

    def close(self) -> None:
        with self._lock:
            if self._owns_database and self._database is not None:
                self._database.dispose()
                self._database = None


def _tool_json(record: ToolAuditRecord) -> str:
    return json.dumps(
        {
            "tool_call_id": record.tool_call_id,
            "task_id": record.task_id,
            "step_id": record.step_id,
            "tool_name": record.tool_name,
            "tool_version": record.tool_version,
            "status": record.status.value,
            "latency_ms": record.latency_ms,
            "timestamp": record.timestamp.isoformat(),
            "attempt": record.attempt,
            "error_code": record.error_code,
            "principal_id": record.principal_id,
            "policy_decision": record.policy_decision,
            "reason_code": record.reason_code,
            "security_finding_codes": list(record.security_finding_codes),
        },
        sort_keys=True,
    )


def _tool_record(payload: str) -> ToolAuditRecord:
    raw = json.loads(payload)
    return ToolAuditRecord(
        tool_call_id=raw["tool_call_id"],
        task_id=raw["task_id"],
        step_id=raw["step_id"],
        tool_name=raw["tool_name"],
        tool_version=raw["tool_version"],
        status=ToolResultStatus(raw["status"]),
        latency_ms=raw["latency_ms"],
        timestamp=datetime.fromisoformat(raw["timestamp"]),
        attempt=raw["attempt"],
        error_code=raw["error_code"],
        principal_id=raw.get("principal_id"),
        policy_decision=raw.get("policy_decision"),
        reason_code=raw.get("reason_code"),
        security_finding_codes=tuple(raw.get("security_finding_codes", ())),
    )


def _workflow_json(record: WorkflowAuditRecord) -> str:
    return json.dumps(
        {
            "event_id": record.event_id,
            "event": record.event,
            "task_id": record.task_id,
            "plan_id": record.plan_id,
            "plan_version": record.plan_version,
            "timestamp": record.timestamp.isoformat(),
            "step_id": record.step_id,
            "tool_name": record.tool_name,
            "attempt": record.attempt,
            "status": record.status,
            "duration_ms": record.duration_ms,
            "error_type": record.error_type,
            "evidence_ids": list(record.evidence_ids),
            "artifact_id": record.artifact_id,
            "metadata": record.metadata.root,
        },
        sort_keys=True,
    )


def _workflow_record(payload: str) -> WorkflowAuditRecord:
    raw = json.loads(payload)
    return WorkflowAuditRecord(
        event_id=raw["event_id"],
        event=raw["event"],
        task_id=raw["task_id"],
        plan_id=raw["plan_id"],
        plan_version=raw["plan_version"],
        timestamp=datetime.fromisoformat(raw["timestamp"]),
        step_id=raw["step_id"],
        tool_name=raw["tool_name"],
        attempt=raw["attempt"],
        status=raw["status"],
        duration_ms=raw["duration_ms"],
        error_type=raw["error_type"],
        evidence_ids=tuple(raw["evidence_ids"]),
        artifact_id=raw["artifact_id"],
        metadata=JsonObject(raw["metadata"]),
    )
