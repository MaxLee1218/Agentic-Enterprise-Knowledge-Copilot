"""Tenant-scoped repository for approved non-secret MCP connections."""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from sqlalchemy import select

from copilot.contracts import MCPConnection
from copilot.contracts.validators import utc_now
from copilot.persistence.database import PersistenceDatabase, coerce_database
from copilot.persistence.models import MCPConnectionRow


class MCPConnectionRepository:
    def __init__(
        self,
        database_path: PersistenceDatabase | Path | None = None,
        *,
        initialize_schema: bool = True,
    ) -> None:
        self._items: dict[tuple[str, str], MCPConnection] = {}
        self._lock = RLock()
        self._database, self._owns_database = coerce_database(
            database_path, initialize_schema=initialize_schema
        )

    def save(self, connection: MCPConnection, *, tenant_id: str) -> None:
        """Upsert an approved configuration; the contract contains references, never secrets."""
        if not tenant_id:
            raise ValueError("tenant_id is required")
        payload = connection.model_dump_json()
        _reject_secret_payload(payload)
        key = (tenant_id, connection.connection_id)
        with self._lock:
            if self._database is None:
                self._items[key] = connection
                return
            with self._database.session() as session:
                row = session.get(MCPConnectionRow, key)
                if row is None:
                    row = MCPConnectionRow(
                        tenant_id=tenant_id,
                        connection_id=connection.connection_id,
                        server_id=connection.server.server_id,
                        namespace=connection.namespace,
                        transport=connection.transport.value,
                        payload_json=payload,
                        updated_at=utc_now(),
                    )
                    session.add(row)
                else:
                    row.server_id = connection.server.server_id
                    row.namespace = connection.namespace
                    row.transport = connection.transport.value
                    row.payload_json = payload
                    row.updated_at = utc_now()

    def get(self, connection_id: str, *, tenant_id: str) -> MCPConnection:
        key = (tenant_id, connection_id)
        with self._lock:
            if self._database is None:
                try:
                    return self._items[key]
                except KeyError as exc:
                    raise KeyError("MCP connection was not found") from exc
            with self._database.session() as session:
                row = session.get(MCPConnectionRow, key)
                if row is None:
                    raise KeyError("MCP connection was not found")
                return MCPConnection.model_validate_json(row.payload_json)

    def list(self, *, tenant_id: str) -> tuple[MCPConnection, ...]:
        with self._lock:
            if self._database is None:
                return tuple(
                    value
                    for (owner_tenant, _), value in sorted(self._items.items())
                    if owner_tenant == tenant_id
                )
            with self._database.session() as session:
                payloads = session.scalars(
                    select(MCPConnectionRow.payload_json)
                    .where(MCPConnectionRow.tenant_id == tenant_id)
                    .order_by(MCPConnectionRow.connection_id)
                )
                return tuple(MCPConnection.model_validate_json(item) for item in payloads)

    def close(self) -> None:
        if self._owns_database and self._database is not None:
            self._database.dispose()
            self._database = None


def _reject_secret_payload(payload: str) -> None:
    lowered = payload.lower()
    for marker in ('"access_token"', '"refresh_token"', '"password"', '"client_secret"'):
        if marker in lowered:
            raise ValueError("Raw credentials cannot be persisted in MCP configuration")


__all__ = ["MCPConnectionRepository"]
