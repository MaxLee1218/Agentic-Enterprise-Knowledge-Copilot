# ADR-020: Chat-First Task Workspace

## Status

Accepted

## Date

2026-09-02

## Context

The implemented React frontend is a governed execution console. It asks users to choose a Use
Case, output format, optional maximum steps, and approval preference; it presents Task history as
a table and foregrounds lifecycle metadata, plan steps, Evidence IDs, and tool details. This is
operationally useful but asks ordinary users to understand internal contracts and adds UI fields
for every business domain.

The backend now has a durable asynchronous Task runtime and interactive clarification. A Task can
survive Queue/Worker restart, suspend without a Worker, accept multiple bounded clarification
rounds on the same Task, resume through Understanding, wait for exact approval, and publish
verified Artifacts. These durable facts can support a conversational projection without creating a
new chat authority.

Two implementation gaps are material. First, omitting the current browser `task_type` selector
falls back to the trusted caller's preselected `purpose`; current Task Understanding does not
resolve Supplier versus AP from natural language. Second, public Task detail exposes only a safe
summary and the current pending clarification, not the historical facts needed to rebuild a full
thread after refresh.

Supplier Quality v1.2 and AP v1.1 business behavior remains frozen. AP's production decision is
still `NOT READY`. The UI decision cannot weaken domain, policy, approval, Evidence, verifier,
async runtime, security, evaluation, or release boundaries.

## Decision

Adopt a chat-first, Task-centric governed workspace with these rules:

1. One Task is one conversation thread and `task_id` is its only identity.
2. `+ New task` opens an unpersisted draft at `/`; the first valid message submits `POST /v1/tasks`
   and navigates to `/tasks/:taskId` after `202 Accepted`.
3. The normal user workflow contains one natural-language composer. It does not expose or send a
   Use Case, output format, maximum steps, approval configuration, business-scope form, role,
   tenant, tool, or model selection.
4. Backend Task Understanding gains a typed, non-executable supported-domain resolution before
   domain-specific understanding. It resolves exactly one enabled domain, asks for clarification
   when genuinely ambiguous, or rejects unsupported/unauthorized work. It intersects all
   candidates with current trusted authority and cannot grant permissions.
5. An explicit PDF/JSON request is extracted into typed understanding. Omission uses a
   deterministic PDF default for both frozen domains; an LLM does not choose the default.
6. Missing fields use the existing ADR-019 clarification state machine and the shared composer.
   The frontend does not implement another clarification workflow.
7. Approval and cancellation remain explicit structured actions. Natural-language text cannot
   perform either transition.
8. Task history moves to a collapsible sidebar. Evidence, execution, Audit, and reports remain
   available through lazy detail overlays and Artifact cards.
9. Conversation content is a read projection of existing TaskRequest, clarification, state event,
   approval, TaskResult, and Artifact facts. No generic Conversation aggregate or message table is
   introduced.
10. `GET /v1/tasks` becomes a lightweight summary contract, while the existing
    `GET /v1/tasks/{task_id}` detail path gains a versioned task-scoped interaction projection.
    There is no `/conversations` or generic message write API.
11. The first implementation uses existing TanStack Query polling and performs no token streaming,
    SSE, WebSocket, file upload, long-term memory, or general chat.
12. Terminal Tasks are read-only. Another business goal requires New Task.

The complete normative presentation, responsive, accessibility, security, migration, testing,
and acceptance contract is frozen in
[`docs/design/conversational-task-workspace.md`](../design/conversational-task-workspace.md).

## Alternatives Considered

### Keep forms as the primary interface

Rejected because forms expose internal Task schema, duplicate Task Understanding, do not scale to
additional governed domains, and make multi-round clarification feel like dynamic configuration.

### Hide the selector but let the frontend infer `task_type`

Rejected because the untrusted browser would still choose an execution-relevant domain, clients
could disagree, and unsupported or ambiguous requests could silently enter the wrong manifest.

### Put multiple Tasks in one conversation

Rejected because it would add cross-Task memory, mixed authorization, retention, recovery, and
ownership semantics that have not been designed.

### Add a generic Conversation model and `chat_messages` table

Rejected because there is no multiple-Task thread or long-term chat use case, and existing durable
records already own every required interaction fact. A second write model would create competing
truth and a larger sensitive-data retention surface.

### Create `/conversations` and `/messages` APIs

Rejected because Task is the conversation identity. A lightweight Task list plus enriched Task
detail projection is smaller, preserves authorization, and avoids duplicate routing concepts.

### Keep approval buttons but also accept “yes”

Rejected because approval is a bound governance action, not conversational content. Explicit
controls are necessary to communicate intent and preserve backend validation/audit semantics.

### Remove Evidence and steps from the product

Rejected because auditability and evidence-backed output are core product values. Their visual
priority changes, not their authority or availability.

### Add token streaming or a realtime channel

Rejected because the runtime emits durable Task states and governed interactions rather than an
ordinary chat-completion stream. Polling is already implemented and sufficient for the first
workspace version.

## Consequences

Users can state a business goal and answer missing information without learning internal enums or
forms. New domains no longer require another initial form section. The Task remains a clean
authorization, persistence, recovery, and retention boundary.

The next implementation stage must make contract-first backend changes before removing the
selector: two-stage supported-domain understanding, a deterministic output default, a lightweight
Task list DTO, and a versioned interaction projection on Task detail. OpenAPI and generated
TypeScript types must change together. No production code is changed by this ADR itself.

Conversation reconstruction is reliable across refresh and tabs because it reads durable facts.
The read service must apply existing tenant/owner/assignment permissions and output minimization
before projection. The projection cannot authorize execution or become a write store.

The current nested detail routes require redirects or overlay-compatible handling during rollout.
Component and browser coverage expands substantially, while existing async runtime, Supplier, AP,
approval, Evidence, Artifact, security, and evaluation gates remain mandatory.

The simpler interface does not broaden capability. Unsupported requests still fail before an
executable Plan, AP remains local/synthetic and production `NOT READY`, and the system is not a
general chatbot.

## Related Documents

- [Conversational Task Workspace frozen contract](../design/conversational-task-workspace.md)
- [Current frontend architecture](../frontend-architecture.md)
- [Async runtime architecture](../async-runtime-architecture.md)
- [ADR-009: Multi-Domain Capability Manifests](ADR-009-multi-domain-capability-manifests.md)
- [ADR-012: Asynchronous Task Submission](ADR-012-async-task-submission-model.md)
- [ADR-019: Durable Interactive Clarification](ADR-019-interactive-clarification-resume.md)
- [Supplier Quality v1.2 baseline](../design/design_baseline.md)
- [Accounts Payable baseline](../use-cases/accounts-payable/design-baseline.md)
- [Accounts Payable Stage 12 review](../use-cases/accounts-payable/stage-12-production-readiness-review.md)

