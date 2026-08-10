# MCP Operations

## Configuration and connection approval

Set `MCP_ENABLED=true` and enable only the required role with `MCP_CLIENT_ENABLED` or
`MCP_SERVER_ENABLED`. HTTP server mode additionally requires issuer, audience and a signing key of
at least 32 bytes. Configure exact hosts/origins, export names and credential environment-name
allowlists. Connection documents contain `credential_reference` such as
`env:APPROVED_MCP_TOKEN`, never the credential value.

Approve a connection only after reviewing canonical server identity and endpoint, transport,
namespace ownership, tenant/scope/capability allowlists, schema bounds, data classification,
approval requirements and incident owner. For stdio also review the absolute executable, exact
arguments, working directory and environment.

## Starting and inspecting

Run migrations before the service:

```bash
alembic upgrade head
python scripts/run_mcp_server.py --tenant TENANT-A
python scripts/inspect_mcp_connection.py config/approved-connection.json
python scripts/smoke_mcp.py config/approved-connection.json
```

The HTTP server binds to `127.0.0.1` by default. Public binding requires explicit configuration,
an approved reverse proxy/TLS boundary and a deployment security review. Inspect health through
the service process/readiness system and MCP lifecycle/connection/failure/latency/reconnect metrics.

## Rotation, revocation and outage

Credential rotation is reference-preserving: update the approved secret source, revoke active
sessions, then reconnect. Reconnect always resolves the credential again and revalidates identity,
tenant, scope, origin and capability schemas. Never put a replacement token in a URL or config file.

For revocation, remove the connection/export rule and call manager revocation; the registry
namespace is removed before future execution. During an outage, preserve task state, return the
typed unavailable/timeout result, apply only idempotent bounded retry and let the workflow decide
replan/partial failure. Do not silently switch servers or widen scope.

## Incident response

1. Disable the affected connection or export and stop new sessions.
2. Rotate credential references and revoke upstream tokens.
3. Preserve tenant-scoped MCP invocation metadata, tool/workflow audit and trace IDs.
4. Check origin/server identity, scope/tenant decisions, approval IDs, schema digests and evidence.
5. Re-enable only after safety tests and connection approval are repeated.

## Protocol upgrade

The default is `2025-11-25` with SDK `>=1.29,<2.0`. A later revision needs a new ADR, SDK changelog
and threat review, internal contract compatibility analysis, stdio/HTTP/OAuth contract tests,
client/server interoperability and safety evaluations, migration review, staged rollout and an
explicit return path to the pinned version. Do not change `LATEST_PROTOCOL_VERSION` implicitly.

## Rollback

Disable MCP feature flags first; the Stage 0–17 vertical slice remains operational. Revoke
connections/exports, drain or terminate sessions, restore the prior application image/config and
verify native regression gates. Preserve MCP tables for forensics by default. Database downgrade
drops Stage 18 tables and is allowed only after backup, retention review and explicit approval:

```bash
alembic downgrade 20260808_0002
```
