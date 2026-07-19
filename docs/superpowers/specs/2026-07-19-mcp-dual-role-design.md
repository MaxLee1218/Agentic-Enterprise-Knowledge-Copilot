# Dual-Role MCP Integration Design

**Date:** 2026-07-19

**Status:** Approved for implementation planning

## 1. Objective

Add Model Context Protocol support after the existing four roadmap phases are complete. The Copilot will operate in both directions:

- As an MCP host/client that connects to approved external MCP servers and imports their capabilities into the existing governed tool system.
- As an MCP server that exposes explicitly approved Copilot tools, resources, and prompts to external MCP clients.

The MCP layer must reuse the repository's existing contracts, policy engine, approval workflow, tool executor, evidence ledger, audit repositories, and observability. It must not create an alternate execution path around those controls.

## 2. Scope

The design includes:

- MCP lifecycle management and capability negotiation.
- Isolated client sessions with a one-to-one client-to-server relationship.
- MCP server support for tools, resources, and prompts.
- MCP client handling for external tools, resources, prompts, sampling, elicitation, and roots where explicitly enabled.
- Standard stdio and Streamable HTTP transports.
- Remote HTTP authorization, origin validation, scopes, server allowlists, and credential references.
- Capability import and export adapters.
- Evidence, audit, policy, approval, testing, evaluation, and operational requirements.
- Repository scaffolding and repository-wide contributor instructions.

The initial protocol baseline is MCP revision `2025-11-25`. A later revision requires an explicit compatibility review, contract tests, and an architecture decision record before becoming the default.

## 3. Non-Goals

The initial MCP phase will not:

- Replace the internal tool registry or executor with MCP-native execution.
- Implement a custom MCP transport.
- Persist plaintext credentials or access tokens.
- Automatically trust or enable every capability advertised by an external server.
- Expose all internal tools by default.
- Allow one external server to observe another server's session, capabilities, prompts, resources, or results.
- Implement a standalone MCP gateway service or separate deployment unless operational evidence later justifies that split.

## 4. Architecture Decision

MCP will be implemented as a dedicated `src/copilot/mcp/` package. This keeps protocol lifecycle, transport behavior, and SDK dependencies isolated from business tools and API routes.

Two alternatives were rejected:

1. Splitting client behavior under `tools/mcp/` and server behavior under `api/mcp/` would distribute shared lifecycle and protocol concerns across unrelated layers.
2. Creating a separately deployed MCP gateway would introduce premature service, deployment, and distributed-consistency boundaries while the repository remains a scaffold.

### 4.1 Client-side flow

```text
MCP Server Configuration
  -> Connection Policy
  -> Credential Resolution
  -> Transport Connection
  -> Protocol Initialization
  -> Capability Negotiation
  -> Import Approved Capabilities
  -> Internal Tool Registry
  -> Policy / Approval / Execution / Evidence
```

Each external MCP server receives a dedicated client session. Imported tools use a stable server namespace to prevent name collisions. External capability metadata is converted into internal contracts before registration. Invocation remains subject to internal authorization, approval, timeouts, evidence capture, and audit requirements.

### 4.2 Server-side flow

```text
MCP Client Request
  -> Transport Authentication
  -> Origin and Session Validation
  -> Capability Scope Check
  -> Internal Policy Engine
  -> Approval when required
  -> Existing Tool Executor
  -> Evidence and Audit Recording
  -> MCP Response
```

Only explicitly exported capabilities are advertised. The server adapter translates MCP requests into internal contracts and translates sanitized internal results into MCP responses. Protocol handling cannot call business databases, retrieval systems, analytics functions, or report renderers directly.

## 5. Repository Changes

All scaffold files listed below will initially be empty. `AGENTS.md` will contain the implementation rules and roadmap changes. Empty test directories are created locally as architectural placeholders.

```text
src/copilot/
├── contracts/
│   └── mcp.py
├── policies/
│   └── mcp_access.py
├── persistence/
│   ├── mcp_connection_repository.py
│   └── mcp_session_repository.py
└── mcp/
    ├── __init__.py
    ├── config.py
    ├── protocol.py
    ├── lifecycle.py
    ├── capabilities.py
    ├── errors.py
    ├── client/
    │   ├── manager.py
    │   ├── session.py
    │   ├── connection_registry.py
    │   ├── capability_importer.py
    │   ├── sampling_handler.py
    │   ├── elicitation_handler.py
    │   └── roots_provider.py
    ├── server/
    │   ├── server.py
    │   ├── capability_exporter.py
    │   ├── tool_provider.py
    │   ├── resource_provider.py
    │   ├── prompt_provider.py
    │   └── authorization.py
    ├── transports/
    │   ├── base.py
    │   ├── stdio.py
    │   └── streamable_http.py
    └── security/
        ├── connection_policy.py
        ├── origin_validator.py
        ├── credential_provider.py
        └── scope_mapper.py

scripts/
├── run_mcp_server.py
├── inspect_mcp_connection.py
└── smoke_mcp.py

evaluation/evaluators/
├── mcp_interoperability.py
└── mcp_safety.py

tests/
├── unit/mcp/
├── integration/mcp/
├── contract/mcp/
└── smoke/mcp/

docs/
├── mcp-architecture.md
├── mcp-security.md
└── mcp-operations.md
```

## 6. Component Responsibilities

### 6.1 Shared MCP components

- `contracts/mcp.py` defines stable internal connection, capability, invocation, provenance, and error contracts. MCP SDK types must not cross this boundary.
- `mcp/config.py` defines server definitions, transport selection, timeouts, feature flags, allowlists, and credential references.
- `mcp/protocol.py` is the only direct adapter to the selected MCP Python SDK and protocol-version-specific types.
- `mcp/lifecycle.py` manages initialization, capability negotiation, readiness, shutdown, and session recovery.
- `mcp/capabilities.py` normalizes and validates negotiated capability descriptors.
- `mcp/errors.py` maps protocol, transport, authorization, timeout, validation, and remote execution failures to internal typed errors.

### 6.2 MCP client components

- `client/manager.py` owns configured client instances without sharing session state between servers.
- `client/session.py` owns the lifecycle and negotiated state for one server connection.
- `client/connection_registry.py` stores active connection metadata and health, not credentials.
- `client/capability_importer.py` imports approved server capabilities through the internal tool registry and policy engine.
- `client/sampling_handler.py`, `elicitation_handler.py`, and `roots_provider.py` implement optional client features that remain disabled unless explicitly authorized.

### 6.3 MCP server components

- `server/server.py` composes the protocol server and delegates all business execution.
- `server/capability_exporter.py` selects and maps only explicitly approved internal capabilities.
- `server/tool_provider.py`, `resource_provider.py`, and `prompt_provider.py` adapt internal contracts to MCP primitives.
- `server/authorization.py` integrates remote authorization, scopes, audience validation, and session identity with existing policies.

### 6.4 Transport and security components

- `transports/base.py` defines the transport boundary used by lifecycle code.
- `transports/stdio.py` starts or serves local subprocess communication without writing non-protocol content to stdout.
- `transports/streamable_http.py` handles HTTP sessions, supported content types, streaming, resumability, and protocol-version headers.
- `security/connection_policy.py` enforces server allowlists, approved commands, endpoints, tenants, and capability policies.
- `security/origin_validator.py` prevents unsafe HTTP origins and DNS rebinding exposure.
- `security/credential_provider.py` resolves secret references at runtime without persisting or logging secret values.
- `security/scope_mapper.py` maps MCP authorization scopes to internal permissions and approval requirements.

## 7. Security Model

- External MCP servers, clients, resources, prompts, tool descriptions, and tool results are untrusted input.
- Imported capabilities require explicit server and capability approval before registration.
- Exported capabilities use deny-by-default selection and least-privilege scopes.
- Remote servers must be allowlisted by canonical identity and endpoint.
- Streamable HTTP must validate the Origin header, authentication token, intended audience, scopes, tenant binding, and session binding.
- Access tokens must never appear in URLs, logs, traces, model context, artifacts, or persisted session data.
- stdio commands must be fixed and approved. Arguments, working directories, inherited environment variables, and subprocess lifetime must be constrained.
- Sampling and elicitation are disabled by default and require per-server and per-capability authorization.
- Side-effecting exported tools require explicit policy rules and human approval where existing risk rules demand it.
- External MCP content must not override system instructions, policy decisions, approval requirements, or tool contracts.
- Session restoration requires fresh credential, permission, capability, and tenant validation.

## 8. Failure Handling

- One external server connection failure must not prevent the Copilot from starting or using other approved servers.
- Protocol version or capability incompatibility must fail the affected session with a structured reason.
- Invalid capability schemas must be rejected before registry import.
- Transport failures, timeouts, invalid responses, and remote tool errors must map to typed internal errors.
- Retries must be bounded and limited to safe or explicitly idempotent operations.
- Non-idempotent operations must not retry silently.
- Partial discovery must not result in partially registered capability sets; import is atomic per server negotiation.
- External error details must be sanitized before being shown to a user or another MCP participant.
- Server-side internal exceptions, credentials, database details, and unrestricted evidence must not cross the MCP boundary.

## 9. Testing Strategy

### Unit tests

- Capability normalization, import, export, and namespace collision behavior.
- Scope mapping, allowlists, origin validation, and credential-reference handling.
- Lifecycle transitions, timeouts, cancellation, retry classification, and error conversion.
- Export filtering and deny-by-default behavior.

### Contract tests

- Protocol initialization and capability negotiation for the pinned revision.
- Tools, resources, prompts, sampling, elicitation, roots, notifications, and progress messages when supported.
- Internal-to-MCP and MCP-to-internal schema compatibility.
- Unsupported capability and version behavior.

### Integration tests

- stdio client/server communication.
- Streamable HTTP communication, authentication, origin validation, session handling, and reconnect behavior.
- Multiple external server isolation and capability namespaces.
- Policy, approval, evidence, audit, and observability integration.

### Smoke and safety tests

- Local Copilot MCP client to Copilot MCP server round trip.
- Malicious server metadata, prompt injection, oversized payloads, invalid JSON-RPC, token leakage attempts, cross-tenant access, and privilege escalation.
- Controlled degradation when one of several configured servers is unavailable.

Continuous integration must eventually run actionlint, Ruff, pytest, MCP contract tests, and an isolated MCP smoke suite.

## 10. Evaluation Framework

MCP evaluation will measure:

- Connection and initialization success rate.
- Capability discovery and mapping accuracy.
- Imported tool selection and argument accuracy.
- Exported capability authorization accuracy.
- Protocol and schema error rate.
- p50 and p95 invocation latency.
- Timeout, reconnect, and session recovery rate.
- Policy and approval routing accuracy.
- Cross-server and cross-tenant isolation failures.
- Prompt-injection resistance and sensitive-data leakage rate.
- Evidence and audit completeness for MCP-originated execution.

Every evaluation run must record the protocol revision, SDK version, transport, server implementation, capability set, authorization mode, configuration, dataset revision, and code revision.

## 11. AGENTS.md Changes

The repository-wide instructions will be updated without adding a new top-level numbered section. MCP requirements will be integrated into the existing 13-section structure:

- Project Overview and Product Vision: add governed protocol interoperability.
- System Architecture: add the dual-role MCP boundary and data flows.
- Agent Design Principles: add protocol isolation and untrusted external capability rules.
- Repository Structure: add all approved scaffold paths.
- Development Rules and Coding Standards: prohibit SDK-type leakage and direct protocol-to-business execution.
- Testing Requirements: add MCP unit, integration, contract, smoke, interoperability, and safety coverage.
- Evaluation Framework: add MCP reliability, isolation, latency, and security metrics.
- Data & Security Rules: add allowlists, OAuth, origin, token, stdio, namespace, sampling, and elicitation controls.
- Future Extension Guidelines: define protocol-revision and capability-extension rules.
- Roadmap: add `Phase 5: MCP Interoperability` after production hardening.
- Common Tasks: add procedures for connecting an MCP server and exposing a capability through the MCP server.

## 12. Roadmap Placement

MCP is Phase 5 and begins only after the completion criteria for Phases 1 through 4 are met:

1. Enterprise RAG and evidence foundation.
2. Agent graph, planning, execution, and memory/checkpointing.
3. Governed database, analytics, and reporting tools.
4. Authentication, tenant isolation, monitoring, deployment, and feedback loops.
5. MCP Interoperability: dual-role client/server lifecycle, transports, authorization, capability import/export, interoperability, operations, and safety evaluation.

Phase 5 is not complete until both directions pass contract, integration, smoke, security, and evaluation gates with traceable evidence.

## 13. Acceptance Criteria

The scaffolding and guidance change is complete when:

- Every approved MCP directory exists locally.
- Every approved MCP placeholder file exists and is zero bytes.
- No existing working file is overwritten or emptied.
- `AGENTS.md` remains an English document with the existing 13 numbered sections.
- `AGENTS.md` documents the dual-role architecture, security rules, testing requirements, evaluation metrics, Phase 5 roadmap, and common MCP tasks.
- Repository structure documentation accurately lists the new MCP package and supporting paths.
- Existing GitHub Actions workflow files remain valid.
- Fresh verification confirms the expected files, empty placeholder sizes, documentation sections, and clean formatting.
