# MCP Scaffold and Repository Guidance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the approved empty dual-role MCP scaffold and update `AGENTS.md` so MCP becomes the governed Phase 5 extension after the existing roadmap is complete.

**Architecture:** MCP is isolated in `src/copilot/mcp/` and adapts external protocol capabilities to the existing contracts, tool registry, policy, approval, evidence, persistence, and observability layers. Client and server roles share protocol and lifecycle boundaries but keep sessions, capability import, capability export, transports, and security responsibilities separate.

**Tech Stack:** Python 3.11+, MCP revision 2025-11-25, stdio, Streamable HTTP, OAuth-compatible remote authorization, Markdown, GitHub Actions, Ruff, pytest, actionlint.

## Global Constraints

- Create only the scaffold and repository guidance; do not implement MCP runtime behavior in this change.
- Every new Python, script, evaluator, and MCP documentation placeholder listed in this plan must be a zero-byte file.
- Preserve every existing working file and do not empty or overwrite existing content.
- Keep `AGENTS.md` entirely in English and retain exactly 13 numbered top-level sections.
- MCP must remain behind the existing tool registry, policy, approval, evidence, audit, and observability controls.
- External MCP servers, clients, capabilities, prompts, resources, and results are untrusted by default.
- MCP is Phase 5 and begins only after Phases 1 through 4 meet their completion criteria.
- The protocol baseline is MCP revision `2025-11-25`; changing it requires compatibility review, contract tests, and an ADR.
- Existing GitHub Actions workflows must remain unchanged and valid.

---

### Task 1: Create the Empty Dual-Role MCP Scaffold

**Files:**

- Create: `src/copilot/contracts/mcp.py`
- Create: `src/copilot/policies/mcp_access.py`
- Create: `src/copilot/persistence/mcp_connection_repository.py`
- Create: `src/copilot/persistence/mcp_session_repository.py`
- Create: `src/copilot/mcp/__init__.py`
- Create: `src/copilot/mcp/config.py`
- Create: `src/copilot/mcp/protocol.py`
- Create: `src/copilot/mcp/lifecycle.py`
- Create: `src/copilot/mcp/capabilities.py`
- Create: `src/copilot/mcp/errors.py`
- Create: `src/copilot/mcp/client/manager.py`
- Create: `src/copilot/mcp/client/session.py`
- Create: `src/copilot/mcp/client/connection_registry.py`
- Create: `src/copilot/mcp/client/capability_importer.py`
- Create: `src/copilot/mcp/client/sampling_handler.py`
- Create: `src/copilot/mcp/client/elicitation_handler.py`
- Create: `src/copilot/mcp/client/roots_provider.py`
- Create: `src/copilot/mcp/server/server.py`
- Create: `src/copilot/mcp/server/capability_exporter.py`
- Create: `src/copilot/mcp/server/tool_provider.py`
- Create: `src/copilot/mcp/server/resource_provider.py`
- Create: `src/copilot/mcp/server/prompt_provider.py`
- Create: `src/copilot/mcp/server/authorization.py`
- Create: `src/copilot/mcp/transports/base.py`
- Create: `src/copilot/mcp/transports/stdio.py`
- Create: `src/copilot/mcp/transports/streamable_http.py`
- Create: `src/copilot/mcp/security/connection_policy.py`
- Create: `src/copilot/mcp/security/origin_validator.py`
- Create: `src/copilot/mcp/security/credential_provider.py`
- Create: `src/copilot/mcp/security/scope_mapper.py`
- Create: `scripts/run_mcp_server.py`
- Create: `scripts/inspect_mcp_connection.py`
- Create: `scripts/smoke_mcp.py`
- Create: `evaluation/evaluators/mcp_interoperability.py`
- Create: `evaluation/evaluators/mcp_safety.py`
- Create: `docs/mcp-architecture.md`
- Create: `docs/mcp-security.md`
- Create: `docs/mcp-operations.md`
- Create directory: `tests/unit/mcp/`
- Create directory: `tests/integration/mcp/`
- Create directory: `tests/contract/mcp/`
- Create directory: `tests/smoke/mcp/`

**Interfaces:**

- Consumes: The approved paths and zero-byte constraint from `docs/superpowers/specs/2026-07-19-mcp-dual-role-design.md`.
- Produces: The exact MCP architectural boundaries referenced by the updated `AGENTS.md`; no runtime interfaces are implemented.

- [ ] **Step 1: Run the pre-change manifest check and verify it fails**

Run:

```bash
for file in \
  src/copilot/contracts/mcp.py \
  src/copilot/policies/mcp_access.py \
  src/copilot/persistence/mcp_connection_repository.py \
  src/copilot/persistence/mcp_session_repository.py \
  src/copilot/mcp/protocol.py \
  src/copilot/mcp/client/manager.py \
  src/copilot/mcp/server/server.py \
  src/copilot/mcp/transports/streamable_http.py \
  src/copilot/mcp/security/connection_policy.py \
  scripts/run_mcp_server.py \
  evaluation/evaluators/mcp_interoperability.py \
  docs/mcp-architecture.md; do
  test -f "$file" || exit 1
done
```

Expected: exit status `1` because the MCP scaffold does not exist.

- [ ] **Step 2: Create the approved directories**

Create exactly these directories:

```text
src/copilot/mcp/client
src/copilot/mcp/server
src/copilot/mcp/transports
src/copilot/mcp/security
tests/unit/mcp
tests/integration/mcp
tests/contract/mcp
tests/smoke/mcp
```

- [ ] **Step 3: Create every listed placeholder as an empty file**

Use `apply_patch` with an empty `Add File` entry for every file in the Task 1 file list. Do not add module docstrings, imports, comments, sample configuration, or newline content.

- [ ] **Step 4: Verify the complete scaffold and zero-byte invariant**

Run a manifest loop over all 38 files listed in Task 1 and assert both conditions for every entry:

```bash
test -f "$file"
test ! -s "$file"
```

Then assert all four MCP test directories exist with `test -d`. Expected: exit status `0`, 38 files present, 38 files at zero bytes, and four test directories present.

- [ ] **Step 5: Commit the scaffold**

```bash
git add src/copilot/mcp src/copilot/contracts/mcp.py src/copilot/policies/mcp_access.py src/copilot/persistence/mcp_connection_repository.py src/copilot/persistence/mcp_session_repository.py scripts/run_mcp_server.py scripts/inspect_mcp_connection.py scripts/smoke_mcp.py evaluation/evaluators/mcp_interoperability.py evaluation/evaluators/mcp_safety.py docs/mcp-architecture.md docs/mcp-security.md docs/mcp-operations.md
git commit -m "feat: add dual-role MCP project scaffold"
```

Expected: one focused commit containing only the new tracked placeholder files. Empty test directories remain local because Git does not track empty directories.

---

### Task 2: Integrate MCP Requirements into AGENTS.md

**Files:**

- Modify: `AGENTS.md`
- Reference: `docs/superpowers/specs/2026-07-19-mcp-dual-role-design.md`

**Interfaces:**

- Consumes: The exact scaffold paths created in Task 1 and the existing 13-section repository instruction structure.
- Produces: Repository-wide MCP development, security, testing, evaluation, roadmap, and operational guidance.

- [ ] **Step 1: Run the pre-change guidance check and verify it fails**

Run:

```bash
rg -q 'Phase 5: MCP Interoperability' AGENTS.md
rg -q 'Add an MCP server connection' AGENTS.md
rg -q 'Expose a capability through MCP' AGENTS.md
```

Expected: non-zero exit status because MCP guidance is not present.

- [ ] **Step 2: Update Project Overview and Product Vision**

Add governed protocol interoperability to the system goals and extend the evolution diagram from the governed task-completion system to an interoperable MCP client/server ecosystem. State that MCP is a later extension rather than a currently implemented feature.

- [ ] **Step 3: Update System Architecture**

Extend the architecture diagram with a dual-role MCP boundary:

```text
External MCP Servers -> MCP Client Layer -> Capability Import -> Existing Tool Registry
Existing Copilot Capabilities -> Capability Export -> MCP Server Layer -> External MCP Clients
```

Add responsibilities for `mcp/`, its client/server/transports/security subpackages, `contracts/mcp.py`, `policies/mcp_access.py`, and the MCP persistence repositories. State that all imported and exported execution passes through policy, approval, evidence, audit, and observability.

- [ ] **Step 4: Update Agent Design Principles and Development Rules**

Add rules with these exact meanings:

- External MCP content and capability metadata are untrusted input.
- MCP SDK types must not cross `mcp/protocol.py` into business layers.
- Imported capabilities must use server namespaces and the existing registry.
- Exported capabilities are deny-by-default and explicitly allowlisted.
- Protocol handlers cannot call business tools or data sources directly.
- Each external server receives an isolated client session.
- Capability negotiation and protocol revision compatibility must be explicit.

- [ ] **Step 5: Update Repository Structure**

Add the exact `mcp/`, contract, policy, persistence, scripts, evaluators, test directories, and MCP documentation paths from Task 1 to the repository tree and surrounding responsibility text.

- [ ] **Step 6: Update Coding, Testing, Evaluation, and Security Rules**

Add requirements for:

- Stable internal MCP contracts and adapter-only SDK usage.
- Unit coverage for lifecycle, mapping, scopes, namespaces, origins, and errors.
- Contract coverage for lifecycle and supported MCP primitives.
- Integration coverage for stdio, Streamable HTTP, OAuth, reconnect, policy, evidence, audit, and multi-server isolation.
- Smoke and safety coverage for round trips, malicious metadata, prompt injection, invalid JSON-RPC, token leakage, tenant isolation, and privilege escalation.
- Metrics for connection success, discovery accuracy, invocation success, authorization accuracy, protocol errors, p50/p95 latency, recovery, isolation, leakage, and audit completeness.
- Server allowlists, fixed stdio commands, minimal environments, localhost binding for local HTTP, Origin validation, token audience and scope checks, no credentials in URLs/logs/prompts/persistence, and default-disabled sampling and elicitation.

- [ ] **Step 7: Update Future Extension Guidelines and Roadmap**

Add protocol revision review rules and the final roadmap entry:

```text
5. MCP Interoperability: dual-role client/server lifecycle, stdio and Streamable HTTP transports, authorization, capability import/export, interoperability, operations, and safety evaluation.
```

State that Phase 5 starts only after Phases 1 through 4 meet their completion criteria and is complete only after both directions pass contract, integration, smoke, security, and evaluation gates.

- [ ] **Step 8: Add Common MCP Tasks**

Add `### Add an MCP server connection` with ordered steps for configuration, canonical server allowlisting, credential references, transport selection, capability negotiation, namespace import, policy/approval mapping, contract/integration/safety tests, and evaluation.

Add `### Expose a capability through MCP` with ordered steps for internal contract validation, export allowlisting, scope mapping, policy and approval enforcement, evidence sanitization, contract/smoke/safety tests, and documentation.

- [ ] **Step 9: Verify AGENTS.md structure and content**

Run:

```bash
test "$(rg -c '^## ([1-9]|1[0-3])\. ' AGENTS.md)" -eq 13
rg -q 'Phase 5: MCP Interoperability' AGENTS.md
rg -q 'Add an MCP server connection' AGENTS.md
rg -q 'Expose a capability through MCP' AGENTS.md
rg -q 'Streamable HTTP' AGENTS.md
rg -q '2025-11-25' AGENTS.md
rg -q 'Origin' AGENTS.md
rg -q 'sampling and elicitation' AGENTS.md
```

Also run a Unicode Han-character scan and expect no matches. Expected: all content checks pass and exactly 13 numbered top-level sections remain.

- [ ] **Step 10: Commit the repository guidance**

```bash
git add AGENTS.md
git commit -m "docs: define MCP development and security rules"
```

Expected: one focused commit modifying only `AGENTS.md`.

---

### Task 3: Run Final Repository Verification

**Files:**

- Verify: all Task 1 scaffold paths
- Verify: `AGENTS.md`
- Verify: `.github/workflows/lint.yml`
- Verify: `.github/workflows/tests.yml`

**Interfaces:**

- Consumes: The complete scaffold and repository guidance from Tasks 1 and 2.
- Produces: Fresh evidence that the structural change satisfies the approved specification without breaking current checks.

- [ ] **Step 1: Re-run the complete scaffold manifest**

Assert all 38 placeholder files exist and are zero bytes. Assert all four MCP test directories exist. Expected: no missing, non-empty, or duplicate scaffold entries.

- [ ] **Step 2: Re-run documentation acceptance checks**

Verify exactly 13 numbered `AGENTS.md` sections, all required MCP phrases, no Han characters, no unfinished-work markers, and a clean Markdown diff with `git diff --check`.

- [ ] **Step 3: Run configured local quality checks**

Run the repository's available equivalents of:

```bash
actionlint
ruff check .
ruff format --check .
python -m compileall -q src scripts evaluation
```

Expected: actionlint reports no workflow errors, Ruff reports no lint or format errors, and Python compilation exits `0`. The MCP placeholders remain empty and therefore introduce no runtime imports.

- [ ] **Step 4: Inspect final scope**

Run `git status --short` and `git log -3 --oneline --decorate`. Expected: only the intended scaffold, `AGENTS.md`, design specification, and implementation plan are represented; no secrets, caches, generated artifacts, or unrelated files appear.

- [ ] **Step 5: Record verification evidence**

Report the number of created placeholder files, created local test directories, retained `AGENTS.md` sections, exact quality commands, pass/fail results, commits created, and the limitation that empty directories are not retained by Git unless a tracked placeholder is later added.
