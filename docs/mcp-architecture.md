# MCP Architecture

Stage 18 implements MCP revision `2025-11-25` in both directions. It is an optional protocol edge,
not a change to the frozen Supplier Quality Analysis v1.1 workflow.

```text
External MCP Server
  -> isolated MCPProtocolClient session
  -> capability normalization + stable server namespace
  -> ToolRegistry
  -> existing ToolExecutor
  -> existing Policy / Approval / Evidence / Audit / Observability

External MCP Client
  -> authenticated MCPProtocolServer
  -> explicit MCPCapabilityExporter allowlist
  -> existing ToolRegistry + ToolExecutor
  -> existing Policy / Approval / Evidence / Audit / Observability
```

`src/copilot/mcp/protocol.py` is the only production SDK boundary. Client, server, persistence,
policy and tool code use stable contracts from `src/copilot/contracts/mcp.py`. No MCP protocol
handler calls a database, RAG client, analytics function or renderer directly.

## Client lifecycle

The deterministic lifecycle is `CREATED -> CONNECTING -> INITIALIZING -> NEGOTIATING -> READY`.
Reconnect re-resolves credentials, revalidates server/tenant/scope/origin policy, creates a new
isolated SDK session, rediscovers capabilities, and atomically replaces the server namespace.
Revocation removes the namespace before further lookup. Bounded retry requires both a local
`allow_idempotent_retry` rule and a read-only, non-destructive, idempotent capability declaration;
business replan remains owned by the agent workflow.

One active imported namespace is bound to one tenant session at a time. A second tenant cannot
reuse or replace that namespace while it is active; deployments needing concurrent tenant access
must configure distinct approved connection IDs/namespaces or isolate workers per tenant.

Tools, resources and prompts are normalized with schema size/depth/node/property limits. External
names receive deterministic collision-safe local names; the external server identity, connection,
transport, endpoint fingerprint, revision, server version and schema digest remain attached.

## Server lifecycle

The server advertises only explicit `MCPExportRule` entries visible to the authenticated tenant and
scope. Invocation visibility and execution authorization are separate checks. The provider creates
the same `ToolCall` and mandatory `ExecutionContext` used by native execution and delegates to the
existing executor. Resources and prompts use independent explicit allowlists; internal system
prompts are not exported.

## Transports and compatibility

- stdio uses a fixed absolute executable, fixed arguments and working-directory allowlists; no
  shell command string is accepted.
- Streamable HTTP uses the SDK session manager, request-size/idle bounds, authentication,
  canonical origin/host controls and DNS-rebinding protection. Plain HTTP is loopback-only.
- Protocol revision is `2025-11-25`; SDK range is `>=1.29,<2.0`. See ADR-008 before upgrading.
