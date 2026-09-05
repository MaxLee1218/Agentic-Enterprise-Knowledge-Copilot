# Conversational Task Workspace Frontend Architecture

**Status:** implemented
**Date:** 2026-09-02
**Authority:** [frozen workspace contract](design/conversational-task-workspace.md) and
[ADR-020](adr/ADR-020-chat-first-task-workspace.md)

## 1. Purpose and authority

The React frontend is a chat-first workspace for governed enterprise Tasks. One Task is presented
as one conversation and `task_id` remains its only durable identity. This presentation layer does
not create a Conversation aggregate, message table, alternative workflow engine, or authorization
boundary.

The backend remains authoritative for domain resolution, lifecycle transitions, trusted scope,
permissions, policy, approvals, clarification, Evidence, verification, Artifact publication, and
downloads. The browser never chooses an execution domain or sends identity, tenant, role, tool,
model, data-source, or business-scope authority.

## 2. Runtime topology

```text
Browser
  -> one frontend origin
  -> Nginx
       /          -> compiled React SPA
       /api/*     -> FastAPI (prefix stripped)
  -> existing service / policy / repository / agent / Queue / Worker boundaries
```

Local Vite development proxies `/api` to `http://127.0.0.1:8000`. Production remains a static
bundle behind unprivileged Nginx. Frontend code has no direct connection to PostgreSQL, RAG,
business databases, Artifact storage, Queue, Worker, or model providers.

## 3. Technology and state ownership

| Concern | Choice |
|---|---|
| UI | React 19, TypeScript, Vite |
| Routing | React Router |
| Server state and polling | TanStack Query |
| Public API types | FastAPI OpenAPI, `openapi-typescript`, `openapi-fetch` |
| Component tests | Vitest, Testing Library, MSW |
| Browser tests | Playwright |
| Styling | repository-owned CSS and design tokens |

TanStack Query owns remote state. React state is limited to draft text, open drawers/dialogs,
sidebar presentation, and an in-flight Idempotency-Key. Critical interaction state is rebuilt from
the server after refresh. No Redux/Zustand store, credential storage, chat memory, or competing
request client is introduced.

## 4. Information architecture

| URL | Meaning |
|---|---|
| `/` | unpersisted New Task welcome and composer |
| `/tasks/:taskId` | refresh-safe conversation projection for one Task |
| `/system` | operational liveness/readiness view, separate from ordinary work |

Legacy `/tasks`, `/tasks/new`, task overview, report, Evidence, and approval deep links redirect to
the canonical workspace route. Evidence and execution are lazy drawers; approval is an inline
structured card; verified reports are Artifact cards in the conversation.

The desktop shell uses a collapsible dark-green history sidebar. Mobile uses a modal task-history
drawer with a focus trap, Escape dismissal, and focus return. History is grouped deterministically
into Today, Yesterday, Previous 7 days, and Older, with server pagination.

## 5. Submission and supported-domain resolution

The first valid composer message performs:

```text
POST /v1/tasks
Idempotency-Key: <per-draft UUID>
{"task": "<natural language>"}
```

The browser sends no `task_type`, output selector, execution limit, approval preference, or scope
form. It preserves the same Idempotency-Key while retrying the same draft and navigates to
`/tasks/:taskId` only after the existing `202 Accepted` response.

Before domain-specific understanding, the backend deterministically resolves exactly one enabled
domain from the request text, intersects it with the caller's trusted `allowed_task_types`, and
persists and audits its typed reason. Multiple supported-domain matches are ambiguous; no match is
unsupported; unauthorized matches are denied. None falls back to caller `purpose`.

An explicit PDF or JSON request is extracted server-side. If absent, the enabled Supplier Quality
and Accounts Payable manifests use the deterministic PDF default. Conflicting or unsupported
formats fail closed.

## 6. Read projection and API boundaries

`GET /v1/tasks` returns a lightweight owner/tenant-scoped summary page containing only ID, safe
title, lifecycle/runtime status, resolved task type, and creation time. It does not load steps,
Evidence, approval detail, Artifact detail, raw metadata, or tool inputs.

`GET /v1/tasks/{task_id}` returns the ordinary task summary plus the versioned
`task-interaction-projection.v1`. The projection is a sanitized read model over existing
authoritative records:

- initial TaskRequest display text;
- all clarification questions and submitted user responses;
- stable lifecycle phase events;
- minimized approval summaries;
- safe terminal TaskResult summary.

Ordering uses persisted timestamps plus deterministic tie-breakers. Artifact metadata remains on
the existing Artifact collection. Approval details, Evidence, and execution steps remain lazy
authorized reads. There is no `/conversations` endpoint or generic message-write API.

The type lifecycle remains:

```text
FastAPI/Pydantic
  -> scripts/export_frontend_openapi.py
  -> frontend/openapi/openapi.json
  -> openapi-typescript
  -> frontend/src/api/generated/schema.d.ts
  -> typed feature queries and mutations
```

## 7. Conversation lifecycle

The stream merges the persisted initial request, quiet phase events, clarification rounds,
approval summaries, result, and Artifact cards. Polling uses the existing status-aware cadence:
active tasks poll frequently, human-wait states poll slowly, and terminal states stop.

The composer is enabled only for an unresolved clarification belonging to the current Task. A
response is natural-language text sent to the existing clarification endpoint and does not create
a Task. During execution, approval, and terminal states the composer is disabled with explicit
copy. Multi-round clarification remains on the same URL and draft text survives typed failures or
stale-state conflicts.

Approvals are never conversational commands. A pending inline card lazily loads authorized detail
and exposes explicit Approve, Edit and resubmit, and Reject controls. Backend editable-field,
scope, stale-version, and permission checks remain authoritative. Cancellation is likewise an
explicit confirmed action and not a message.

## 8. Evidence, execution, and Artifacts

Evidence and execution records are secondary, read-only detail drawers opened on demand. Evidence
shows only the minimized public contract; execution shows safe step purpose and status, not tool
arguments or hidden checkpoint/runtime authority. Technical failure detail is shown only from the
safe public error summary.

Completed tasks fetch Artifact metadata and render one card per verified file. Open/download URLs
are constructed only from encoded Task and Artifact IDs under the same-origin API. Storage paths,
credentials, checksums used as authority, and unverified bytes never enter presentation state.

## 9. Accessibility and resilient interaction

The shell and pages use landmarks, semantic headings, labelled controls, text status labels, skip
navigation, and visible focus. Drawers and confirmation dialogs trap focus, close on Escape, and
return focus to the opener. The composer implements Enter-to-send, Shift+Enter for newline, IME
composition protection, a visible send button, deterministic length validation, and draft
preservation on failure. Responsive rules keep the stream readable and the composer reachable at
desktop and mobile widths; reduced-motion preferences are honored.

## 10. Security and intentional exclusions

All user, retrieval, tool, and model text is treated as untrusted. Conversation display is derived
through existing redaction and output-safety guards. Browser messages cannot grant authority,
bypass approvals, select tools, or broaden data scope.

This stage intentionally adds no chat database, cross-Task memory, multiple Tasks in one thread,
streaming/SSE/WebSocket transport, file upload, natural-language approval/cancellation, Queue or
Worker changes, second lease system, alternate execution path, or claim of broader production
readiness.

## 11. Verification

Contract/unit coverage verifies domain and format resolution, safe projections, lightweight list
shape, polling, draft/idempotency behavior, clarification, approval, terminal read-only behavior,
lazy detail, accessibility interactions, and responsive history. Playwright covers the mocked
workspace, real two-round Accounts Payable clarification through the hermetic FastAPI/Worker
driver, refresh reconstruction, Artifact rendering, and mobile drawer behavior. The full backend,
frontend static/build, OpenAPI, documentation, evaluation, and browser-inspection gates remain
release requirements.
