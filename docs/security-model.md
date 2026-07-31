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
