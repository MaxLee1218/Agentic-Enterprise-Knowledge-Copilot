"""Tenant-isolated MCP session, recovery, and invocation metadata repositories."""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from copilot.contracts import MCPInvocationMetadata, MCPSession
from copilot.persistence.database import PersistenceDatabase, coerce_database
from copilot.persistence.mcp_connection_repository import _reject_secret_payload
from copilot.persistence.models import MCPInvocationRow, MCPSessionRow


class MCPSessionRepository:
    def __init__(
        self,
        database_path: PersistenceDatabase | Path | None = None,
        *,
        initialize_schema: bool = True,
    ) -> None:
        self._items: dict[tuple[str, str], MCPSession] = {}
        self._lock = RLock()
        self._database, self._owns_database = coerce_database(
            database_path, initialize_schema=initialize_schema
        )

    def save(self, snapshot: MCPSession) -> None:
        payload = snapshot.model_dump_json()
        _reject_secret_payload(payload)
        key = (snapshot.tenant_id, snapshot.session_id)
        with self._lock:
            if self._database is None:
                self._items[key] = snapshot
                return
            with self._database.session() as session:
                row = session.get(MCPSessionRow, key)
                if row is None:
                    row = MCPSessionRow(
                        tenant_id=snapshot.tenant_id,
                        session_id=snapshot.session_id,
                        connection_id=snapshot.connection_id,
                        server_id=snapshot.server_id,
                        state=snapshot.state.value,
                        payload_json=payload,
                        updated_at=snapshot.updated_at,
                        expires_at=snapshot.expires_at,
                    )
                    session.add(row)
                else:
                    if row.connection_id != snapshot.connection_id:
                        raise ValueError("MCP session cannot change connection ownership")
                    row.state = snapshot.state.value
                    row.payload_json = payload
                    row.updated_at = snapshot.updated_at
                    row.expires_at = snapshot.expires_at

    def get(self, session_id: str, *, tenant_id: str) -> MCPSession:
        key = (tenant_id, session_id)
        with self._lock:
            if self._database is None:
                try:
                    return self._items[key]
                except KeyError as exc:
                    raise KeyError("MCP session was not found") from exc
            with self._database.session() as session:
                row = session.get(MCPSessionRow, key)
                if row is None:
                    raise KeyError("MCP session was not found")
                return MCPSession.model_validate_json(row.payload_json)

    def list(self, *, tenant_id: str) -> tuple[MCPSession, ...]:
        with self._lock:
            if self._database is None:
                return tuple(
                    item
                    for (owner_tenant, _), item in sorted(self._items.items())
                    if owner_tenant == tenant_id
                )
            with self._database.session() as session:
                payloads = session.scalars(
                    select(MCPSessionRow.payload_json)
                    .where(MCPSessionRow.tenant_id == tenant_id)
                    .order_by(MCPSessionRow.updated_at)
                )
                return tuple(MCPSession.model_validate_json(item) for item in payloads)

    def close(self) -> None:
        if self._owns_database and self._database is not None:
            self._database.dispose()
            self._database = None


class MCPInvocationRepository:
    """Append-only minimized audit metadata; arguments and results are deliberately absent."""

    def __init__(
        self,
        database_path: PersistenceDatabase | Path | None = None,
        *,
        initialize_schema: bool = True,
    ) -> None:
        self._items: dict[tuple[str, str], MCPInvocationMetadata] = {}
        self._lock = RLock()
        self._database, self._owns_database = coerce_database(
            database_path, initialize_schema=initialize_schema
        )

    def append(self, metadata: MCPInvocationMetadata) -> None:
        payload = metadata.model_dump_json()
        _reject_secret_payload(payload)
        key = (metadata.tenant_id, metadata.invocation_id)
        with self._lock:
            if self._database is None:
                if key in self._items:
                    raise ValueError("MCP invocation metadata already exists")
                self._items[key] = metadata
                return
            try:
                with self._database.session() as session:
                    session.add(
                        MCPInvocationRow(
                            tenant_id=metadata.tenant_id,
                            invocation_id=metadata.invocation_id,
                            session_id=metadata.session_id,
                            task_id=metadata.task_id,
                            trace_id=metadata.trace_id,
                            payload_json=payload,
                            timestamp=metadata.timestamp,
                        )
                    )
            except IntegrityError as exc:
                raise ValueError("MCP invocation metadata already exists") from exc

    def list(
        self, *, tenant_id: str, task_id: str | None = None
    ) -> tuple[MCPInvocationMetadata, ...]:
        with self._lock:
            if self._database is None:
                return tuple(
                    item
                    for (owner_tenant, _), item in sorted(self._items.items())
                    if owner_tenant == tenant_id and (task_id is None or item.task_id == task_id)
                )
            with self._database.session() as session:
                statement = select(MCPInvocationRow.payload_json).where(
                    MCPInvocationRow.tenant_id == tenant_id
                )
                if task_id is not None:
                    statement = statement.where(MCPInvocationRow.task_id == task_id)
                payloads = session.scalars(statement.order_by(MCPInvocationRow.timestamp))
                return tuple(MCPInvocationMetadata.model_validate_json(item) for item in payloads)

    def close(self) -> None:
        if self._owns_database and self._database is not None:
            self._database.dispose()
            self._database = None


__all__ = ["MCPInvocationRepository", "MCPSessionRepository"]
