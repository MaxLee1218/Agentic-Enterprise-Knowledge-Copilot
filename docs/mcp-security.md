# MCP Security

MCP metadata, prompts, resources, schemas and results are untrusted data. They cannot supply
instructions, identity, tenant, roles, scopes, approvals or policy decisions.

## Controls

- Deny by default: connections, canonical server IDs, namespaces, origins, tenants, capabilities,
  capability types, scopes and exports require explicit configuration.
- HTTP authentication validates JWT signature, issuer, audience, expiry, issued-at, subject,
  client, user, tenant and scopes. Session ownership is bound by the SDK session manager.
- Credentials are resolved from approved references at runtime. Raw tokens are never stored in
  connection/session/invocation tables, URLs, logs, traces, evidence, prompts or artifacts.
- HTTP endpoints reject URL credentials/query/fragment, unsafe redirects, non-approved hosts,
  unsafe DNS resolution and unapproved `Origin` values. Client HTTP ignores ambient proxy
  variables so canonical origin policy is not silently rerouted.
- stdio permits fixed executables/arguments/directories with minimal non-secret environments and
  bounded subprocess lifetime.
- Imported names and JSON Schemas are normalized and bounded; `$ref`, oversized, too-deep and
  collision-prone definitions fail closed.
- Sampling and elicitation are absent by default. Explicit policy is required; elicitation rejects
  secret/credential fields. Roots are tenant-specific and reject `/`, home, credential directories,
  `.env`, traversal and symlink escape.
- Export is explicit. Discovery, registration, authorization and execution permission are distinct.
- Every governed result is output-guarded. Evidence and audit preserve origin/provenance while
  excluding raw request/result payloads from minimized MCP invocation metadata.

## Threat tests

Hermetic suites cover invalid JSON-RPC, hostile Origin, SSRF/DNS rebinding, malicious descriptions,
schema abuse, prompt injection text, token leakage, invalid audience, missing scopes, cross-tenant
access, cross-server leakage, privilege escalation, approval bypass, timeout and cancellation.

For an incident: revoke the connection/export, rotate the referenced credential, terminate affected
sessions, preserve tenant-scoped invocation/audit metadata, inspect trace IDs and only reconnect
after server identity, endpoint, scopes and capability schemas are reapproved.
