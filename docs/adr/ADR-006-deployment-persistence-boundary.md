# ADR-006: Deployment Persistence and Migration Boundary

## Status

Accepted

## Date

2026-08-07

## Context

The Stage 0–16 runtime stored authoritative Task, Evidence, Approval, Audit, Artifact metadata,
lease, and recovery data in tables created independently inside several SQLite adapters. The
enterprise business Database Tool also used a setting named `DATABASE_URL`. Reusing that URL for
Copilot state would expose internal tables to the wrong trust boundary. SQLite remains useful for
isolated tests, but it is not the deployment persistence target and application workers must not
silently evolve a production schema.

## Decision

- Keep `DATABASE_URL` exclusively for the governed, read-only enterprise business Database Tool.
- Introduce `PERSISTENCE_DATABASE_URL` exclusively for Copilot-owned Task, State, Result,
  Evidence, Approval, Audit, lease, and Artifact metadata.
- Use one synchronous SQLAlchemy `PersistenceDatabase` and one shared declarative metadata root
  for SQLite and PostgreSQL. Repository/application interfaces and frozen domain contracts remain
  unchanged.
- Use SQLite plus explicit development/test schema helpers for fast local execution. Production
  requires PostgreSQL and disables automatic schema creation.
- Use Alembic as the deployment migration authority for Copilot-owned tables. API worker startup
  validates the schema but never runs `create_all` or `alembic upgrade`.
- Use the official synchronous LangGraph PostgreSQL saver in PostgreSQL deployments. Its
  vendor-owned checkpoint tables are initialized by the same single-process deployment migration
  command after Alembic; they remain recovery snapshots, not domain authority.
- Keep Artifact bytes in the configured filesystem/volume and store only immutable metadata in
  the Copilot database.
- Apply bounded startup connection retries and conservative PostgreSQL pool settings from typed
  configuration. Repository sessions rollback and close on failure.

## Alternatives Considered

Using the enterprise business `DATABASE_URL` for Copilot tables was rejected because it would
collapse the internal persistence and read-only business-data trust boundaries. Keeping separate
raw SQLite implementations and adding a parallel PostgreSQL repository was rejected because
business services would need backend branches. Running migrations in every API worker was
rejected because concurrent schema changes are unsafe. Storing Artifact bytes in PostgreSQL was
rejected because Stage 17 requires metadata migration, not an object-storage redesign.

## Consequences

Deployments must run `python -m copilot.persistence.migrate` before starting a new application
version. PostgreSQL backup does not include Artifact file content, so the Artifact volume needs an
independent backup. Alembic and the LangGraph saver have separate schema ownership within the same
database and migration command. There is no atomic transaction across RAG, the enterprise
business database, Artifact filesystem, and Copilot PostgreSQL.

## Related Documents

- [Architecture](../architecture.md)
- [Deployment](../deployment.md)
- [Operations](../operations.md)
- [Frozen design baseline](../design/design_baseline.md)
