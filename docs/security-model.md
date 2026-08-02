# Security Model

The frozen design documents remain authoritative for identity, tenant/data scope, policy,
approval, tool execution, Evidence, and verification.

## LLM boundary

- User text, retrieved content, database text, and tool output are untrusted data.
- System rules, ToolRegistry definitions, authenticated scope, limits, and state are supplied in
  separate trusted prompt fields.
- The model cannot create a tool, authorize a call, alter approval, relax read-only behavior,
  raise max steps, submit raw SQL, or execute code.
- Task Understanding cannot infer tenant, user, data scope, or approval from natural language.
- Planner sees only a minimized permission-filtered ToolRegistry manifest.
- Every model output is parsed into a strict Pydantic model and then passes deterministic domain,
  plan, policy, and executor validation.

Normal logs contain correlation identifiers, prompt/schema versions, provider/model, latency,
usage, attempt, status, and typed error only. They exclude API keys, Authorization headers, full
prompts/responses, raw rows, and full document content. Provider exceptions contain safe fixed
messages and never echo response bodies.

DeepSeek credentials are loaded through `Settings` as `SecretStr` and are required only for the
real provider. Mock mode requires no secret. Retry is restricted to transient transport/status
conditions and cannot turn authentication, permission, configuration, schema, or business errors
into authorized actions.

## Human approval boundary

- Approval identity comes from `TrustedCallerContext`; request bodies cannot set `resolved_by`,
  tenant, role, plan, tool, scope, or action fingerprint.
- Reading and resolving an approval requires the bound tenant and `required_role`. The checked-in
  demo identity is a local adapter and must be replaced by deployed authentication.
- Approval binds task, plan version, step, tool/version, Input Schema, controlled scope, complete
  arguments, validity window, and original/resolved action fingerprints.
- `EDIT` is fail-closed: complete replacement only, current Registry/Schema revalidation, frozen
  field allowlist, and limit reduction only. Free-form advice cannot authorize inferred changes.
- Repository compare-and-swap, TaskState compare-and-swap, and the execution lease prevent two
  decisions or two resumptions from executing the same target.
- Tool authorization and final Safety Verification independently re-check the resolved binding.
- Audit stores identifiers, actor/tenant/trace, decision/reason, outcomes, and parameter hashes;
  it excludes full parameters, secrets, raw SQL, document bodies, and database rows.
