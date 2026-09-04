# Conversational Task Workspace — Frozen Product and Frontend Contract

**Status:** FROZEN / ACCEPTED
**Decision date:** 2026-09-02
**Implementation status:** IMPLEMENTED — VERIFIED
**Applies to:** Supplier Quality Analysis v1.2 and Accounts Payable Analysis v1.1

This document freezes the target product, interaction, projection, frontend, and minimum API
contract for the chat-first frontend implementation. The normative contract remains frozen; the
implementation status annotation records the explicitly authorized delivery without changing the
accepted product, runtime, security, or domain decisions below.

The frozen product position is:

> The frontend is a conversational workspace for governed enterprise tasks. Each Task is
> presented to the user as one conversation thread. The user expresses business intent and
> supplies missing business information through natural language, while authorization, policy,
> planning, execution, approval, evidence, verification, and artifacts remain structured and
> deterministic behind the conversational interface.

The workspace borrows only the familiar interaction pattern of a collapsible history sidebar,
one main conversation surface, and a bottom composer. It retains the Enterprise Knowledge
Copilot identity, enterprise-green visual language, and project governance semantics. It must not
copy OpenAI or ChatGPT logos, wordmarks, proprietary icons, exact colors, or pixel-level design.

## 1. Scope

This freeze covers:

- the product model and information architecture;
- the one-Task/one-thread mapping;
- New Task and welcome behavior;
- natural-language task intake and supported-domain resolution;
- clarification, approval, cancellation, status, error, Artifact, Evidence, and execution-detail
  presentation;
- the task-scoped interaction projection;
- routing, responsive behavior, and accessibility;
- minimum API impact and security boundaries;
- migration, testing, acceptance, and implementation gates.

This is a presentation evolution over the existing governed Task system. It does not modify
Supplier or AP formulas, taxonomy, query templates, analytics, reports, policies, permissions,
approval rules, Evidence requirements, verifier rules, tool allowlists, or MCP access rules.

## 2. Goals

1. Let a user state a supported business goal without learning `task_type`, a planner schema,
   report enums, maximum-step controls, or internal execution steps.
2. Make missing-information recovery a natural-language, multi-round interaction on the same
   durable Task.
3. Preserve explicit, auditable controls for approval and cancellation.
4. Present task history, progress, results, and failure in a quiet conversation-first workspace
   while retaining drill-down access to Evidence, execution, Audit, and Artifacts.
5. Keep the Task database and typed domain contracts authoritative; the UI and its projection are
   never execution authority or long-term memory.
6. Allow an additional governed use case to be added without adding another field group to the
   initial user experience.

## 3. Non-Goals

- A general ChatGPT clone or general chat mode.
- Long-term memory or preferences carried between Tasks.
- One conversation containing multiple Tasks.
- Cross-task context, conversation summarization, or arbitrary continuation of a completed Task.
- Token-by-token assistant streaming.
- WebSocket or SSE; the first implementation continues bounded polling.
- File upload, voice, images, web search, slash commands, or attachments.
- Tool, model, MCP, Use Case, tenant, role, or authorization-scope pickers.
- Natural-language approval or cancellation commands.
- Task deletion, archive, or rename unless a separately governed backend contract already exists.
- Multi-Agent behavior.
- A generic `Conversation` domain, generic `chat_messages` table, or a second source of truth.
- Any claim that AP or the whole platform is production-ready.

## 4. Current-State Audit

The audit used the current repository, generated OpenAPI document, implementation tests, Supplier
design v1.2, AP design and Stage 12 review, and async runtime ADRs. Historical screenshots and old
reports were not used as implementation authority.

### 4.1 Frontend

The current frontend is a React 19/TypeScript/Vite execution console using React Router, TanStack
Query, React Hook Form, Zod, a generated OpenAPI client, Vitest, and Playwright. Its current routes
are:

| Current route | Current surface |
|---|---|
| `/` | redirect to `/tasks` |
| `/tasks` | paginated task table with status filter |
| `/tasks/new` | task-creation form |
| `/tasks/:taskId` | task metadata, lifecycle, clarification form, and full step timeline |
| `/tasks/:taskId/evidence` | Evidence page |
| `/tasks/:taskId/report` | Artifact page |
| `/tasks/:taskId/approvals/:approvalId` | approval workbench |
| `/system` | health/readiness page |

The creation form exposes a Use Case selector, output-format selector, optional maximum steps, and
an approval checkbox. The history table exposes IDs, request summary, use case, status, created
time, and Artifact count. Task detail foregrounds trace IDs, task type, counts, internal step IDs,
tool names, dependencies, attempts, and Evidence IDs. This is correct for an execution console but
not the frozen conversational product model.

The current clarification surface is a dynamic structured form. It renders `date`, `date_range`,
`text`, `single_select`, and `multi_select` controls plus an optional free-text answer. The new
default UI removes those controls and uses the common message composer.

The current green design tokens, same-origin `/api` boundary, generated client lifecycle, safe
Artifact download route, typed errors, semantic controls, visible focus, and responsive foundation
are reusable.

### 4.2 Current HTTP and OpenAPI surface

The generated OpenAPI document exposes:

- `POST /v1/tasks`;
- `GET /v1/tasks`;
- `GET /v1/tasks/{task_id}`;
- `GET /v1/tasks/{task_id}/steps`;
- `GET /v1/tasks/{task_id}/evidence`;
- `GET /v1/tasks/{task_id}/artifacts` and the guarded Artifact download route;
- clarification detail and response routes;
- approval detail and resolution routes;
- cancellation;
- process, liveness, and readiness health routes.

`POST /v1/tasks` is already acceptance-only: it returns `202`, `TaskStatus=CREATED`, and
`RuntimeStatus=READY`; the API process does not execute LangGraph. The frontend already navigates
to Task detail and polls authoritative state.

`GET /v1/tasks` is tenant/owner/assignment scoped, bounded to at most 100 records per page, and
ordered by `created_at` descending with task ID as a stable tie-breaker. It returns the full current
`TaskResponse`, but the sidebar needs only summary fields. `TaskResponse` has no `updated_at`.
Therefore the first sidebar groups and orders by `created_at`; it must not pretend to provide
last-updated ordering.

`GET /v1/tasks/{task_id}` exposes a safe 240-character `task_summary`, current state, runtime
state, counts, pending approval ID, and only the current pending clarification. It does not expose
the complete original request, resolved clarification rounds and responses, approval history, or
TaskResult summary. Existing endpoints therefore cannot reliably rebuild a complete conversation
after refresh.

### 4.3 Clarification implementation

Interactive clarification is implemented and matches ADR-019:

- `WAITING_CLARIFICATION` is a nonterminal Task state;
- each `TaskClarification` round is durable, versioned, tenant/Task bound, and has immutable
  history;
- partial answers are valid;
- the Worker can create a later round asking only for remaining information;
- a response returns `202`, atomically moves the same Task to `UNDERSTANDING`, creates a new
  dispatch generation, and never runs the Graph in the API process;
- answer values and free text remain untrusted; current identity and allowed values are
  deterministically revalidated;
- duplicate/stale/concurrent responses use fingerprints and compare-and-swap;
- browser refresh can discover the current pending clarification through Task detail.

The persistence repository can list all rounds, including retained responses. The public API
currently exposes only one round by known ID and the current pending round, so historical
conversation reconstruction is the missing read projection, not a missing clarification state
machine.

### 4.4 Approval, cancellation, Evidence, Artifact, and System

Approval detail and resolution are implemented with explicit `APPROVE`, bounded `EDIT`, and
`REJECT`; role, expiry, schema, plan, scope, fingerprint, and concurrency validation are backend
authority. Approval resolution returns `202` and resumes through a new Worker dispatch.

Cancellation is an explicit authorized endpoint. It is durable and idempotent for an already
cancelled Task, revokes a pending interaction, prevents late publication, and rejects attempts to
cancel other terminal states.

Evidence and Artifact endpoints already provide minimized, authorized read models. Artifact
downloads recheck task ownership, publication, controlled path, size, and checksum. System health
is a separate implemented surface. These capabilities move in presentation but are not removed.

### 4.5 Async runtime

Stages B–H of the frozen async architecture are implemented with PostgreSQL Queue v1. The runtime
uses a transactional outbox, at-least-once delivery, an independent Worker, execution generation,
the extended `workflow_leases` mechanism, heartbeat, monotonic fencing, checkpoint
reconciliation, bounded recovery, backpressure, and polling. Queue messages, Worker memory, and
LangGraph checkpoints are not Task authority. The chat-style implementation must keep this path
and must not restore inline execution for conversational immediacy.

Extended production load/soak, deployment rollout, multi-host Artifact safety, HA, and broader
production readiness remain unproven.

### 4.6 Supported domains and production claims

Both `supplier_quality_analysis.v1` and `accounts_payable_analysis.v1` have enabled domain
capability manifests and share the governed Graph/Registry/Executor path. AP is an implemented
local/synthetic vertical slice. Its Stage 12 release decision remains `NOT READY`, with production
identity/finance authorization, policy ownership, retention/legal hold, coordinated restore,
production-shaped load/soak, provider governance, HA objectives, and organizational sign-off still
blocking.

### 4.7 Domain-resolution gap

The public request makes `task_type` optional, but current intake resolves omission to the trusted
caller's single `purpose`. `TrustedTaskContext` is domain-bound before Task Understanding, and the
LLM understanding adapter selects a domain schema from that already chosen type. In the
multi-domain local identity, the default purpose is Supplier Quality. Consequently, omitting the
browser selector does not currently implement natural-language Supplier/AP domain resolution.

The frozen implementation stage must close this gap at the backend understanding boundary. The
frontend must not infer a domain and secretly continue sending `task_type`.

### 4.8 Output-default gap

The current API can omit `output_format`, but the understanding adapter may then accept the model's
deliverable format. The new product contract requires explicit natural-language extraction when
the user asks for PDF or JSON and a deterministic domain/server default otherwise. Model choice
alone cannot establish a default. The first frozen default for both supported domains is PDF.

## 5. Frozen Product Model

The user-facing model is:

```text
Task T-123
  = Conversation Thread T-123
      original user request
      zero or more clarification rounds
      a small number of phase/status events
      zero or more governed approval interactions
      terminal result or typed failure
      Artifact cards
      Evidence/execution detail affordances
```

This is an identity mapping, not a new aggregate:

```text
conversation_id = task_id
```

No independent Conversation ID is created. A thread begins only when a Task is persisted and ends
when that Task becomes terminal. Completed, failed, and cancelled threads are read-only.

## 6. Twelve Immutable Principles

1. One Task equals one conversation thread.
2. New Task does not persist an empty Task; the first valid user message creates the Task.
3. The primary frontend interaction is natural language.
4. Use Case, Report Format, Maximum Steps, approval configuration, and business-parameter forms
   are removed from the normal user workflow.
5. Task type is resolved by backend Task Understanding, not selected by the user or browser.
6. Missing business information is resolved through bounded multi-round natural-language
   clarification on the same Task.
7. Chat is a presentation layer; TaskContract, authorization, Planner, Policy, Executor, Evidence,
   Audit, and Verifier remain structured and authoritative.
8. Approval and cancellation remain explicit governed actions, never free-form commands.
9. Steps, Evidence, Audit, and Artifacts remain available as secondary details.
10. The collapsible sidebar is authorized Task history; New Task starts a draft workspace.
11. No long-term chat memory, multi-Task conversation, or general chatbot is introduced.
12. Existing Supplier/AP business, security, evaluation, and production-readiness boundaries are
    not weakened.

## 7. Information Architecture

Only three primary visual surface classes remain:

| Surface | Route or trigger | Contract |
|---|---|---|
| Workspace | `/` and `/tasks/:taskId` | sidebar plus one conversation workspace |
| Detail overlays | Evidence, execution, report actions | right drawer on larger screens; accessible full-screen panel where needed |
| System | `/system` | operational health outside all task conversations |

Desktop layout:

```text
+----------------------+-----------------------------------------------+
| collapsible sidebar  | title                         compact status |
| + New task           |                                               |
| Today                | conversation projection                       |
|   AP August review   |                                               |
|   Supplier Q2        |                                               |
| Previous 7 days      |                                               |
|   ...                |                                               |
|                      | [ Message the Agent...              Send ]   |
| System               |                                               |
+----------------------+-----------------------------------------------+
```

The conversation content has a readable maximum width. Exact pixels are implementation-level
design-token choices and are not frozen here.

## 8. Sidebar Contract

The sidebar contains, in order:

1. menu/collapse control;
2. `+ New task`;
3. authorized Task History;
4. `System` anchored near the bottom.

History groups are `Today`, `Yesterday`, `Previous 7 days`, and `Older`, calculated in the user's
presentation timezone from authoritative `created_at`. Each Task row displays a presentation title
and an optional compact status indicator. It does not display tenant, step count, Evidence count,
tool count, raw task type, trace ID, or internal Task ID.

The initial page uses `GET /v1/tasks` with bounded pagination. It does not fetch steps, Evidence,
approval detail, Artifact bytes, or the interaction projection for every row. Loading, no-history,
and history-error states are required. A page of 100 items is the maximum first implementation;
incremental pagination may load more without changing grouping semantics.

Current ordering is newest `created_at` first. Updated-time ordering is not claimed unless a later
API explicitly adds safe `updated_at` to the summary contract.

Desktop starts expanded. Collapse state may be stored in localStorage as a presentation
preference. Collapsed mode retains the menu toggle and New Task affordance but no complete task
titles. Mobile/tablet narrow mode starts closed as an off-canvas drawer; choosing a Task closes it.
No backend preference field is introduced.

### 8.1 Title

Title is non-authoritative presentation metadata. First implementation derives it
deterministically from the output-guarded `task_summary`, optionally using the already trusted
resolved domain label and removing boilerplate. It is length-bounded and falls back to truncated
summary text. It never participates in understanding, planning, authorization, policy, tool
selection, Evidence, or execution.

No LLM title call and no rename endpoint are added in the first implementation.

## 9. New Task and Welcome Contract

`+ New task` navigates to `/` and creates only an in-memory draft/empty workspace. It does not
call `POST /v1/tasks`. A valid first message is trimmed, client-checked for non-empty content and
the public maximum length, then submitted once with an Idempotency-Key:

```text
New Task click != persisted Task
first valid message -> POST /v1/tasks -> 202 -> /tasks/:taskId
```

After acceptance, the new Task appears in the sidebar and the workspace polls server state. The
draft text remains ephemeral until submission. On a transport uncertainty the same
Idempotency-Key is reused; a user choosing an explicit new submission creates a new key.

The empty workspace uses deterministic static copy and never invokes an LLM:

> **Welcome to the Enterprise Knowledge Copilot.**
>
> I can currently help with supplier quality analysis and Accounts Payable compliance and
> exception investigations using authorized enterprise data and approved internal policies.
>
> Describe what you would like me to do in natural language. If any required information is
> missing, I’ll ask before planning or execution.
>
> I can currently generate evidence-backed PDF and JSON reports. All data access remains limited
> to your authorized scope.
>
> **What would you like me to do?**

This describes capability, not production readiness. It must not claim access to production
finance systems or that AP is production-ready.

## 10. Natural-Language Interaction Contract

The normal browser submission body contains the natural-language `task` and no user-selected
`task_type`, `output_format`, `max_steps`, `read_only`, `require_approval`, tenant, role, scope,
tool, or model fields. The public API may retain tightening-only options for compatibility with
non-workspace clients; the workspace does not expose or send them.

The backend flow is frozen as:

```text
untrusted user message
  -> authenticated collection-level intake and immutable TaskRequest
  -> Task Understanding: supported-domain resolution
  -> current-identity authorization intersection
  -> domain-specific typed understanding
  -> clarification when information is missing
  -> complete typed TaskContract
  -> Planner -> Policy -> Approval -> Registry/Executor
  -> Evidence -> Artifact -> Verifier
```

A conversation message never invokes a tool directly. Chat history is never an authorization
source.

### 10.1 Supported-domain resolution

The implementation stage introduces a typed, non-executable `TaskDomainResolution` outcome inside
Task Understanding. Its closed outcomes are:

- `RESOLVED` with exactly one of the two enabled versioned Task types;
- `AMBIGUOUS` when multiple supported domains remain plausible;
- `UNSUPPORTED` when neither supported domain applies.

Resolution considers only supported manifests and untrusted user text. It then intersects the
candidate with current `allowed_task_types` and permissions. It cannot add a role, purpose, data
scope, capability, or tool. A resolved domain selects the existing exact manifest and its
domain-specific understanding schema. `AMBIGUOUS` may create a bounded clarification asking the
user to restate the business goal without presenting an internal enum selector. `UNSUPPORTED` or
an unauthorized resolved domain fails safely before Plan creation.

The domain result and reason code are persisted/audited as task-scoped understanding facts. It is
not supplied by the browser. Existing domain-bound `TrustedTaskContext` must be produced only
after this resolution, or replaced by a versioned two-stage trusted context with the same security
property. The implementation may choose the internal class layout but cannot preserve the current
caller-purpose fallback as domain selection.

### 10.2 Business parameters

Date range, year/quarter, legal entity, supplier scope, output request, and analysis intent come
from the initial message or clarification. Identity, tenant, roles, permitted legal entities,
supplier authorization, data scope, and policy snapshots always come from trusted server context.

### 10.3 Output format

An explicit PDF or JSON request is extracted into a typed value and validated against the resolved
domain manifest. If absent, both frozen domains default deterministically to PDF. A manifest or
server contract owns that default; model preference does not. An unsupported requested format
produces clarification or a typed unsupported-output failure and never a model-selected fallback.

### 10.4 Maximum steps and governance options

`max_steps` remains a server/domain governance control and is not shown. Text such as “use 100
steps” cannot increase the effective limit. Natural language cannot turn off read-only policy,
avoid required approval, grant approval, or select tools. Policy may still tighten constraints.

## 11. Composer State Contract

The bottom composer supports multiline text, `Enter` to send, `Shift+Enter` for newline, visible
send control, accessible label, pending state, error recovery, and preserved draft on a rejected
submission.

| Workspace state | Composer behavior |
|---|---|
| Draft `/` | enabled; first valid message creates a Task |
| `WAITING_CLARIFICATION` | enabled; message responds to the current clarification |
| `CREATED`, `UNDERSTANDING`, `PLANNING`, `EXECUTING`, `RETRYING`, `REPLANNING`, `VERIFYING` | disabled; show “This task is currently executing.”; explicit Cancel remains separate when authorized |
| `WAITING_APPROVAL` | disabled; approval uses the structured card |
| `COMPLETED`, `FAILED`, `CANCELLED` | disabled; show `Start a new task` |

The first implementation does not accept objective changes while a Task runs and does not reopen
a terminal Task for more messages.

## 12. Clarification Contract

A pending clarification is rendered as an Agent message, subtype `clarification`, followed by the
same text composer. Structured date pickers, selects, and business forms are removed from the
default workspace.

The Agent message combines backend question prompts, reasons where useful, and allowed options
from the trusted question contract. For example:

```text
Agent

I need two more pieces of information before I can plan this task:

1. What exact invoice date range should I analyze?
2. Which authorized legal entity should I use?

You are currently authorized for LE-CN-01 and LE-DE-01.
```

Allowed values must come only from `ClarificationQuestion.allowed_values`, which is created from
current trusted scope. The frontend may format those exact values into prose; it cannot enumerate
values from the prompt, cached tenant data, or an unrestricted query.

The composer submits `{answers: {}, message: <text>}` to the current clarification endpoint.
Task Understanding parses candidate business values, deterministic validators bind them to the
question contract and current authority, and valid partial facts are stored in
`ClarificationContext`. If information remains missing, a later round is projected on the same
Task. No frontend clarification state machine is created.

A stale, already answered, cancelled, or concurrently resolved interaction uses the backend 409
contract. The UI says, “This task has already moved forward. Refreshing…” and reloads Task detail.
Out-of-scope values remain deterministically denied even if the model interprets them.

The implementation must preserve the configured maximum clarification rounds and all ADR-019
dispatch, checkpoint, generation, lease, and fencing behavior.

## 13. Status Presentation

The header presents one compact, human label derived from authoritative `TaskStatus +
RuntimeStatus`:

| Authority | User label |
|---|---|
| `CREATED + READY` | Queued |
| `CREATED/UNDERSTANDING` while leased/active | Understanding |
| `WAITING_CLARIFICATION + SUSPENDED` | Waiting for information |
| `PLANNING` | Planning |
| `EXECUTING` | Executing |
| `WAITING_APPROVAL + SUSPENDED` | Waiting for approval |
| `RETRYING` or runtime `WAITING_RETRY` | Retrying |
| `REPLANNING` | Replanning |
| `VERIFYING` | Verifying |
| `COMPLETED` | Completed |
| `FAILED` | Failed |
| `CANCELLED` | Cancelled |

Color is supplementary. Text and screen-reader status are required.

Low-level graph nodes are not messages. The conversation may show only durable, user-useful phase
events such as “Understanding your request…”, “Planning the task…”, “Executing the analysis…”,
and “Verifying the result…”. It never presents node names, step numbers, query fingerprints,
lease/fencing data, checkpoint revisions, repair attempt counters, or tool internals as ordinary
messages.

## 14. Governance Action Contract

### 14.1 Approval

Approval remains an explicit governance action:

```text
Agent

Approval is required before the next controlled action can continue.

+------------------------------------+
| Controlled data access             |
| bounded, backend-projected summary |
|                                    |
| [Reject] [Edit, when allowed]      |
|                         [Approve]  |
+------------------------------------+
```

The card is loaded through the authorized approval endpoint and presents only an appropriate
bounded summary by default. Approve, Edit, and Reject remain semantic buttons. Edit retains the
existing full-replacement, allowlisted-decrease-only behavior for `top_k` or `row_limit`, with a
required reason. Reject retains its required reason. Backend role, scope, expiry, plan version,
tool/schema binding, fingerprints, and CAS remain authoritative.

Typing “yes”, “approve”, or similar text never grants approval. The normal composer is disabled in
`WAITING_APPROVAL`.

### 14.2 Cancellation

An authorized nonterminal Task exposes a separate `Cancel task` or `Stop` action with accessible
confirmation. Typing “cancel” is not a cancellation command. Terminal tasks have no cancel action.

## 15. Artifact, Evidence, Execution, and Audit Presentation

On completion, the Agent presents the safe TaskResult summary and one or more Artifact cards:

```text
Agent

The analysis is complete.

[Open report] [Download PDF] [View evidence]
```

Artifact metadata and bytes continue through existing guarded endpoints. Open may use the report
overlay or safe browser viewer; Download uses the existing controlled download route and checksum
semantics.

Evidence is not emitted as a long sequence of messages. `View evidence` opens a right drawer,
modal, or full-screen mobile panel and lazy-loads existing minimized Evidence. Execution steps and
Audit move behind `View execution details`; the ordinary view groups them into business phases.
An advanced section may show current step-level fields already authorized by the API. Backend
Evidence, Audit, and step records are not deleted or weakened.

## 16. Error and Unsupported Experience

Typed backend errors remain authority. The primary error card uses safe user language and a
secondary `View technical details` affordance for the existing error code, trace ID, and safe
details. Raw stack traces, SQL, credentials, unrestricted payloads, and internal connection data
are never shown.

Examples:

```text
Agent

I couldn't continue this task because the required information could not be validated.

[View technical details]
```

```text
Agent

I couldn't complete this task because access to the requested legal entity is not authorized for
your current scope.

[View details]
```

Unsupported request:

```text
User

Send an email to our supplier asking for a refund.

Agent

This task is not currently supported. I can currently help with supplier quality analysis and
Accounts Payable compliance and exception investigations.
```

No executable unknown Task type or plan is created. MCP is not automatically selected to extend
capability.

## 17. Message Projection Model

Projection, not a new source of truth, is frozen. The frontend presentation model has these closed
kinds:

- `agent_message` with optional `welcome`, `clarification`, `completion`, or `unsupported`
  subtype;
- `user_message`;
- `status_event`;
- `approval_card`;
- `artifact_card`;
- `error_card`.

The mapping is deterministic:

| Authoritative fact | Presentation item |
|---|---|
| static product copy | welcome Agent message |
| authorized projection of `TaskRequest.raw_input` | initial user message |
| each `TaskClarification.questions` | clarification Agent message |
| each retained `TaskClarification.response` | user message |
| selected TaskState/runtime events | status event |
| ApprovalRequest and resolution | approval card/state |
| TaskResult summary | completion/error Agent message |
| Artifact metadata | Artifact card |

Presentation ordering uses persisted timestamps, then deterministic kind/round/ID tie-breakers.
The projection must be stable across refresh and tabs. It cannot authorize transitions, reconstruct
business state, or be written back as a message log.

### 17.1 Minimum API extension

The current data is durable but not all of it is publicly readable. The implementation stage must
split the read model by use:

1. `GET /v1/tasks` returns a lightweight `TaskListItemResponse` containing only Task ID, safe
   summary/title input, status/runtime status, task type when classified, and `created_at`.
2. `GET /v1/tasks/{task_id}` returns `TaskDetailResponse`, preserving current detail fields and
   adding one `interaction_projection` object.

`interaction_projection` is task-scoped and versioned, with at least:

```text
schema_version = task-interaction-projection.v1
initial_user_message { display_text, created_at }
clarification_rounds[] {
  clarification_id, round, status, questions[],
  response_display_text?, created_at, submitted_at?, resolved_at?
}
phase_events[] { phase, occurred_at }
approval_summaries[] {
  approval_id, status, safe_label, resolution_action?,
  created_at, resolved_at?
}
result { final_status, safe_summary }?
```

`display_text`, `safe_label`, and `safe_summary` pass through existing output/redaction policy.
Structured clarification answers without free text receive deterministic, bounded display text;
the persisted response remains authority. Approval summaries never expose credentials or
unrestricted arguments; full governed detail remains at the existing approval endpoint.

The projection is assembled by the application read service from existing TaskRequest,
clarification rounds, state events, approvals, and TaskResult. No new table, generic message
write endpoint, generic conversation endpoint, or interaction event authority is introduced.
Artifact and Evidence data remain lazy-loaded from their existing endpoints.

This enriched existing Task detail path is the minimum extension required for faithful refresh
recovery. If implementation proves an existing persisted event cannot represent one of these
items, the design must be revised before adding storage; implementation may not silently add a
chat-message table.

## 18. Routing and Deep Links

Frozen routes:

| Route | Behavior |
|---|---|
| `/` | deterministic welcome and draft composer |
| `/tasks/:taskId` | one Task conversation; server-backed reconstruction |
| `/system` | dedicated operational health |

Evidence, execution, and report are overlays controlled from the Task route. The implementation
may preserve old nested URLs as redirects or deep-link-compatible overlay routes during migration,
but it must not introduce `/conversations/:id` beside `/tasks/:id`.

Refresh of a queued/running/waiting/terminal Task reloads Task detail, interaction projection, and
then any visible lazy detail. Critical interaction state never exists only in React memory.

If the same Task is open in two tabs, the server wins. A stale clarification or approval response
shows the conflict message, invalidates queries, and reloads rather than overwriting newer state.

## 19. Async Runtime Behavior

The frozen path is:

```text
send first message
  -> POST /v1/tasks with Idempotency-Key
  -> 202 Accepted
  -> navigate /tasks/:taskId
  -> sidebar invalidation
  -> bounded Task-detail polling
  -> Worker-owned execution or suspension
```

Polling continues with TanStack Query. Active runtime polling may remain near the current
two-second interval; human waits may remain near ten seconds; terminal tasks stop automatically.
Exact intervals are implementation configuration, not business contract. No token streaming,
SSE, WebSocket, inline Graph execution, or resident Worker during human waits is added.

## 20. Security Contract

The browser never chooses or supplies authoritative tenant, user, role, allowed domain, data
scope, legal-entity authorization, supplier authorization, policy snapshot, tool, plan, runtime
budget, or approval. Browser text and every projected message are untrusted data.

Prompt text such as “ignore your rules”, “use LE-US-01”, “grant me finance role”, “use 100 steps”,
or “approve this” cannot change identity, scope, limits, tools, policy, or approval state.

Task list, detail projection, clarification, approval, Evidence, execution, and Artifact reads all
reuse current tenant/owner/assignment and permission checks. Projection assembly occurs after
authorization and never performs an unrestricted tenant-wide read. Cache/query keys include Task
identity and are cleared on identity/session changes.

Data minimization prohibits raw tenant identifiers in the ordinary UI, DB connections, full SQL,
credentials, tokens, raw stack traces, unrestricted arguments, and unredacted source content.

No system-prompt or internal-rule editor is exposed. No UI action bypasses Registry, Policy,
Approval, Executor, Evidence, Audit, or Verification.

## 21. Responsive and Accessibility Contract

Desktop, tablet, and mobile are required. Desktop supports expanded/collapsed sidebar. Mobile uses
an accessible modal drawer. Detail drawers become full-screen panels when necessary. The composer
remains reachable above the virtual keyboard, and approval buttons remain visible without
horizontal scrolling.

Implementation must provide:

- keyboard navigation and predictable focus order;
- visible focus and a skip link;
- semantic buttons and landmarks;
- labelled sidebar toggle and drawer close controls;
- focus trap, Escape close, and focus restoration for modal surfaces;
- screen-reader announcements for compact Task status changes without repeated polling noise;
- accessible composer instructions for Enter/Shift+Enter;
- text/icon status indicators so color is never the sole signal;
- reduced-motion support;
- readable contrast and touch target sizes;
- preserved focus and draft text on recoverable errors.

## 22. Migration Map

| Current surface | Frozen target |
|---|---|
| Task creation form | New Task draft welcome plus composer |
| task list page/table | collapsible sidebar history |
| task detail page | main conversation workspace |
| structured clarification form | Agent clarification message plus common composer |
| full Steps timeline | Execution Details drawer/panel |
| Evidence page | Evidence drawer/panel |
| Report page | Artifact card plus report overlay/download |
| approval workbench | inline structured approval card with governed detail/edit surface |
| Cancel button/dialog | explicit workspace Stop/Cancel action |
| System page | retained dedicated `/system` surface |

Old nested URLs must redirect or open the corresponding overlay during rollout so bookmarks do not
silently fail. Current APIs remain until all migrated clients and tests pass. No delete, archive,
rename, upload, model, tool, or MCP action is added as migration convenience.

## 23. Required Conversation Examples

### 23.1 Accounts Payable with clarification

```text
Agent

Welcome to the Enterprise Knowledge Copilot.

I can currently help with supplier quality analysis and Accounts Payable compliance and exception
investigations using authorized enterprise data and approved internal policies.

What would you like me to do?

User

Analyze recent Accounts Payable invoices and generate a PDF report.

Agent

I can do that. I need two pieces of information before planning:

1. What exact invoice date range should I analyze?
2. Which authorized legal entity should I use?

You are currently authorized for LE-CN-01 and LE-DE-01.

User

August 1 through August 31, 2026 for LE-CN-01.

Status  Planning…
Status  Executing…
Status  Verifying…

Agent

The analysis is complete.

[Open report] [Download PDF] [View evidence]
```

### 23.2 Supplier direct task

```text
User

Analyze supplier quality for Q2 2026 and generate a PDF report.

Status  Understanding…
Status  Planning…
Status  Executing…
Status  Verifying…

Agent

The analysis is complete.

[Open report]
```

### 23.3 Unsupported request

```text
User

Send an email to our supplier asking for a refund.

Agent

This task is not currently supported.

I can currently help with supplier quality analysis and Accounts Payable compliance and exception
investigations.
```

### 23.4 Approval

```text
User

Analyze the authorized invoice scope described above.

Agent

Approval is required before the next controlled action.

[Controlled action card]
[Reject] [Edit, when permitted] [Approve]
```

### 23.5 Failed authorization

```text
Agent

I couldn't complete this task because access to the requested legal entity is not authorized for
your current scope.

[View details]
```

## 24. Testing Strategy

The implementation stage must add component, integration, generated-contract, and browser tests.
At minimum:

1. **New AP task plus clarification:** draft does not persist; first natural-language message
   creates one Task; sidebar updates; natural-language partial/full responses reuse that Task;
   completion yields the PDF Artifact.
2. **Supplier direct task:** complete request auto-resolves Supplier without selector and completes.
3. **Switching Tasks:** a running Task and a second draft/Task can be selected through the sidebar;
   server state reconstructs each Task.
4. **Approval:** waiting Task renders explicit card; authorized button resolution resumes the same
   Task; typed “yes” cannot resolve it.
5. **Refresh:** pending clarification, pending approval, running, and completed deep links rebuild
   from server data.
6. **Concurrency:** stale clarification/approval from a second tab produces a conflict and reload.
7. **Unsupported/ambiguous:** no executable unknown plan and no frontend-chosen fallback domain.
8. **Security:** forged role/tenant/scope/options, out-of-scope legal entities, prompt injection,
   and hidden-field manipulation remain denied.
9. **Performance:** history does not fetch Task detail, steps, Evidence, approvals, or Artifact
   bytes; overlays lazy-load their content.
10. **Accessibility/responsive:** keyboard, screen reader status, focus restoration, mobile drawer,
    full-screen details, and composer behavior pass automated and browser checks.

Existing approval/cancellation, async Queue/Worker/fencing/recovery, Supplier, AP, Artifact,
Evidence, and security regression suites remain mandatory.

## 25. Implementation Quality Gates

All of the following must pass before the implementation stage may be called complete:

- clean OpenAPI export and generated TypeScript client (`api:check`);
- TypeScript typecheck;
- ESLint;
- Prettier check;
- Vitest component/integration suite;
- production Vite build;
- Playwright browser suite including the frozen scenarios;
- complete configured backend regression suite;
- Supplier evaluation regression baseline;
- AP evaluation regression baseline;
- security and tenant-isolation regressions;
- async runtime/approval/clarification regressions;
- documentation and architecture checks.

No failing frozen test or baseline may be weakened or deleted to pass this gate.

## 26. Frozen Acceptance Criteria

Implementation is acceptable only when all statements are true:

1. `/` renders deterministic welcome copy and creates no Task before a valid send.
2. The first valid message creates one durable Task via acceptance-only `202` and navigates to
   `/tasks/:taskId`.
3. No Use Case, report-format, maximum-step, approval, Supplier/AP, or business-parameter form is
   present in the normal workflow.
4. Supplier/AP resolution occurs in backend Understanding, is typed and audited, intersects
   current authority, and never trusts the browser.
5. Ambiguous and unsupported requests do not enter executable planning.
6. Missing information uses the existing durable multi-round clarification lifecycle on the same
   Task and the common composer.
7. Both domains use deterministic PDF default when no explicit format is requested.
8. Running and terminal composer behavior matches Section 11.
9. Approval and cancellation remain explicit governed actions.
10. Sidebar history is authorized, lightweight, grouped by `created_at`, and handles loading,
    empty, error, and pagination states.
11. Deep links and refresh reconstruct original request, clarification rounds, approvals, phase
    events, result, and available Artifacts from server authority.
12. Evidence, execution, Audit, and Artifact detail remain accessible and lazy-loaded.
13. The UI exposes no raw authorization, runtime ownership, credentials, SQL, or stack traces.
14. Desktop/tablet/mobile and accessibility requirements pass.
15. Existing Supplier/AP business semantics and all governance/evaluation gates remain unchanged.
16. AP copy remains accurate about its local/synthetic and `NOT READY` production status.
17. No generic Conversation/message persistence, streaming, memory, upload, or general-chat scope is
    introduced.

## 27. Rejected Alternatives

### Keep the task form

Rejected because it exposes internal task contracts, duplicates Task Understanding, scales by
adding fields for every domain, and turns clarification into dynamic form construction.

### Let the frontend classify and send `task_type`

Rejected because it preserves the selector in hidden form, creates divergent clients, and places a
security- and execution-relevant domain decision in an untrusted presentation layer.

### One conversation with multiple Tasks

Rejected because it changes domain ownership, authorization, retention, history, recovery, and
memory semantics without a current use case.

### Generic Conversation and message tables

Rejected because current durable Task, clarification, approval, state-event, result, and Artifact
records already own the facts. Duplicating them creates conflicting truth and new retention risks.

### Approval or cancellation by typed text

Rejected because these are governed state transitions requiring explicit intent, scope binding,
authorization, and audit.

### Delete Evidence and steps for a cleaner UI

Rejected because traceability is a primary product value. They become secondary detail, not absent
data.

### Token streaming, SSE, or WebSockets

Rejected because the runtime is Task-state-driven, not chat-completion-driven, and current polling
already represents durable authority.

### Create a new `/conversations` API

Rejected because Task is the thread identity. The minimum extension enriches the existing Task
detail read model and makes the list response lighter.

### Let the LLM choose the default output format

Rejected because defaults are business/product policy. Both domains deterministically default to
PDF.

## 28. Future Extensions

Future design changes may consider capability-list generation from domain manifests, server-sent
progress, governed task archive/rename, upload, or memory. Each requires its own authorization,
retention, security, contract, migration, testing, and evaluation review. None is implied by this
freeze.

Adding Use Case 3 should require backend domain resolution and manifest/understanding support,
accurate welcome copy, and any genuinely distinct result renderer. It must not require a new
selector or initial form section.

## 29. Implementation Readiness

The target product model, interaction semantics, domain-resolution boundary, projection source,
minimum API change, security rules, migration, tests, and acceptance criteria are fully decided.
There are no unresolved product or architecture decisions in this freeze.

**Implementation Readiness: READY**

READY means the next implementation stage may begin under this contract. It does not mean the
feature is implemented, AP is production-ready, or the platform has passed deployment release
gates.

## 30. Related Documents

- [ADR-020: Chat-First Task Workspace](../adr/ADR-020-chat-first-task-workspace.md)
- [Current frontend architecture](../frontend-architecture.md)
- [Async runtime architecture](../async-runtime-architecture.md)
- [ADR-019: Durable Interactive Clarification](../adr/ADR-019-interactive-clarification-resume.md)
- [Supplier Quality v1.2 baseline](design_baseline.md)
- [Accounts Payable design baseline](../use-cases/accounts-payable/design-baseline.md)
- [Accounts Payable Stage 12 readiness review](../use-cases/accounts-payable/stage-12-production-readiness-review.md)
