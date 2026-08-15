# Frontend and HTTP Contract Audit

**Audit date:** 2026-08-13  
**Scope:** existing browser UI, FastAPI/OpenAPI, identity and tenant boundaries, deployment,
tests, and the frozen Supplier Quality Analysis v1.1 design.

## 1. Audit authority and method

This audit was completed before the React migration. The following sources were treated as the
implementation authority:

- repository-wide `AGENTS.md`;
- all seven frozen documents under `docs/design/`;
- FastAPI routes and Pydantic schemas under `src/copilot/api/`;
- domain enums under `src/copilot/contracts/enums.py`;
- application views and authorization in `src/copilot/services/`;
- identity providers under `src/copilot/security/identity.py`;
- API, integration, approval, artifact, and deployment contract tests;
- the three Compose files, backend Dockerfile, frontend Dockerfile, and Nginx configuration;
- the OpenAPI document produced by `create_app().openapi()` in the repository virtual environment.

The browser-facing deployment adds `/api` as a reverse-proxy prefix. FastAPI itself exposes
`/v1/...` and `/health...`; browser code calls `/api/v1/...` and `/api/health...`.

## 2. Existing frontend

### 2.1 Files

Before migration, `frontend/` contains:

| File | Purpose |
|---|---|
| `index.html` | One-page task submission and result workspace |
| `app.js` | Unbundled DOM rendering and Fetch API integration |
| `styles.css` | Responsive, dependency-free presentation |
| `nginx.conf` | Static serving, security headers, SPA-compatible fallback, `/api/` proxy |
| `Dockerfile` | Single-stage unprivileged Nginx image copying the three static assets |

The tracked legacy directory has no package manifest, lockfile, build system, typed source tree,
generated API client, or JavaScript test suite.

### 2.2 Capability matrix

| Area | State | Finding |
|---|---|---|
| HTML/CSS | Implemented | Semantic form, responsive two-column result layout, visible focus styles |
| Task submission | Implemented | `POST /api/v1/tasks` with task text and output format |
| Task summary | Implemented | Displays task ID, trace ID, status, and summary |
| Steps | Partial | Displays tool, purpose, status, attempts; omits dependencies, timing, errors, and evidence references |
| Evidence | Partial | Displays type/source/summary only; does not classify cards or show exposed lineage fields |
| Artifacts | Implemented | Lists metadata and downloads through the governed API route |
| Approval | Partial | Shows only a pending approval ID and instruction; no detail fetch or decision actions |
| Cancel | Missing | Backend route exists but there is no UI action |
| Status refresh | Partial | Manual refresh only; no polling and no terminal-state strategy |
| Routing/deep links | Missing | One page and one in-memory `activeTaskId`; refresh loses the task |
| Task history | Missing | No UI and no backend list endpoint |
| System status | Partial | Readiness badge only; no liveness/process or dependency view |
| Error handling | Partial | User-visible message and nested `detail` handling, but no typed/status-specific UX |
| Accessibility | Partial | Labels/focus/live regions exist; richer error association and dialog behavior are absent |
| Tests | Missing | No unit, component, accessibility, or browser tests |
| Build/type safety | Missing | No TypeScript, linting, bundling, generated types, or dependency lock |

The legacy UI correctly keeps credentials out of the browser, uses only same-origin `/api` calls,
does not access databases or RAG directly, and never uses an Artifact storage path.

## 3. Actual HTTP API

All task-management endpoints require a trusted caller identity through
`get_caller_context`. In development, the server injects a configured demo identity without
browser headers. In production, a trusted upstream must provide a complete, short-lived, HMAC
signed identity assertion. Tasks are tenant-scoped and additionally restricted to their creating
user. Approval reads/resolutions also require the configured approver role. The frontend must not
offer tenant, user, role, or scope selectors.

Polling suitability below reflects the current synchronous execution model: a normal create call
usually returns at a terminal state; `202` is used when a durable approval interrupts execution.
The read endpoints are safe to poll, but this repository has no background task queue.

| Method and FastAPI path | Request | Success response | Declared statuses | Auth/tenant behavior | Polling and frontend usage |
|---|---|---|---|---|---|
| `POST /v1/tasks` | `NaturalLanguageTaskSubmission`; only `task` required; optional `output_format`, `max_steps`, `read_only`, `require_approval`, `session_id`, `metadata` | `TaskSubmissionResponse` | 201, 202, 422, 500, 503, 504 | Trusted identity creates immutable user/tenant ownership; body cannot supply identity | Mutation only; navigate to returned task; 202 carries `pending_approval_id` |
| `GET /v1/tasks/{task_id}` | Path ID | `TaskResponse` | 200, 403, 404, 422, 500 | Exact tenant and creating user plus `READ_TASK` permission | Primary polling endpoint and task header |
| `GET /v1/tasks/{task_id}/steps` | Path ID | `TaskStepsResponse` | 200, 403, 404, 422, 500 | Same authorized task boundary | Refresh after task/status changes; render execution timeline |
| `GET /v1/tasks/{task_id}/evidence` | Path ID | `TaskEvidenceListResponse` | 200, 403, 404, 422, 500 | Requires authorized task plus `READ_EVIDENCE` | Refresh after task/status changes; render minimized evidence |
| `GET /v1/tasks/{task_id}/artifacts` | Path ID | `ArtifactListResponse` | 200, 403, 404, 422, 500 | Tenant/user task access checked by service | Refresh after task/status changes; report page/list |
| `GET /v1/tasks/{task_id}/artifacts/{artifact_id}` | Path IDs | Streamed bytes (`application/octet-stream` documented; runtime media type is specific) | 200, 403, 404, 410, 422, 500 | Rechecks task ownership, published result, controlled path, size, and checksum | Direct browser download; bytes must not be copied into React state |
| `POST /v1/tasks/{task_id}/cancel` | Path ID; no body | `TaskResponse` | 200, 403, 404, 409, 422, 500 | Requires `CANCEL_TASK`; revokes pending approval | Mutation; invalidate task, steps, evidence, artifacts, approval |
| `GET /v1/tasks/{task_id}/approvals/{approval_id}` | Path IDs | `ApprovalDetailResponse` | 200, 403, 404, 409, 422, 500 | Tenant, task binding, and approver role are checked before full arguments are returned | Fetch only for a known pending approval/deep link |
| `POST /v1/tasks/{task_id}/approvals/{approval_id}` | `ApprovalResolutionRequest` | `ApprovalResolutionResponse` | 200, 400, 403, 404, 409, 422, 500 | Same binding and approver authorization; service resumes checkpoint | Mutation; invalidate approval/task/steps/evidence/artifacts |
| `GET /health` | None | `{status: "ok"}` | 200 | No task identity dependency | Low-frequency process-health display |
| `GET /health/live` | None | `{status: "live"}` | 200 | No task identity dependency | Low-frequency liveness display |
| `GET /health/ready` | None | status, `accepts_tasks`, dependency state map | 200, 503 | No task identity dependency | Low-frequency readiness display; 503 body is still useful data |

There is no `GET /v1/tasks` operation in routes or generated OpenAPI. The repository also has no
task-list port. Task history therefore requires a small, backwards-compatible, user- and
tenant-scoped paginated read capability; the frontend must not access persistence directly.

### 3.1 Error contract caveat

Task, approval, artifact, validation, and internal service handlers normally return
`TaskErrorResponse`:

```text
error_code, message, task_id, trace_id, details
```

The identity dependency currently raises FastAPI `HTTPException(401)` with the error object nested
under `detail`, and 401 is not declared on every OpenAPI operation. The API client/error adapter
must normalize both shapes. This is recorded as an API documentation gap; changing the identity
boundary is not necessary for the frontend migration.

## 4. Task lifecycle contract

The only valid task statuses are:

```text
CREATED
UNDERSTANDING
PLANNING
EXECUTING
WAITING_APPROVAL
RETRYING
REPLANNING
VERIFYING
COMPLETED
FAILED
CANCELLED
```

`COMPLETED`, `FAILED`, and `CANCELLED` are terminal. The frontend must not synthesize statuses or
assume every task traverses a fixed five-step display. `WAITING_APPROVAL` is a durable wait state;
approval rejection, expiry, revocation, and authorized cancellation lead to `CANCELLED` according
to the frozen state machine.

## 5. Step contract

`TaskStepResponse` exposes exactly:

```text
step_id
tool_name
purpose
status
depends_on
attempt_count
retry_count
started_at
completed_at
latency_ms
evidence_ids
error_code
error_message
```

Step status is one of `PENDING`, `SUCCESS`, `BUSINESS_FAILURE`, `TECHNICAL_FAILURE`, `TIMEOUT`,
`PERMISSION_DENIED`, or `CANCELLED`. Tool inputs/outputs, attempt payloads, and internal plan
schemas are intentionally absent. Dependencies and evidence IDs are safe lineage references.

## 6. Evidence contract

The public API exposes three real types: `DOCUMENT`, `DATABASE`, and `CALCULATION`. Every evidence
item contains:

```text
evidence_id, type, source, produced_by, step_id, lineage, confidence,
created_at, query_id, document_source, formula, input_evidence_ids, content_summary
```

This is deliberately smaller than the internal frozen `EvidenceItem` and tool outputs.

- Document UI may show source, `document_source`, producer, confidence, summary, and lineage. Page,
  chunk, version, excerpt, classification, checksum, and RAG trace are not public fields.
- Database UI may show source, producer, `query_id`, summary, and lineage. The actual public value
  is named `query_id`; raw SQL, database name, tables, columns, row count, scope, and dataset
  checksum are not exposed as separate fields.
- Calculation UI may show source, producer, `formula`, `input_evidence_ids`, summary, and lineage.
  Metric value, grouping, precision, warnings, and dataset checksum are not exposed separately.

The UI must label absent metadata honestly instead of extracting it from free text or exposing
internal objects.

## 7. Approval contract

`ApprovalDetailResponse` exposes:

```text
approval_id, task_id, status, step_id, planning_version,
tool_name, tool_version, editable_fields,
proposed_arguments, resolved_arguments, reason,
resolution_action, resolution_reason,
created_at, expires_at, resolved_at, resolved_by
```

Risk level, required role, requester, scope, schema fingerprint, and action fingerprints are not
public fields. Authorization is still enforced by the backend even if the frontend hides actions
after a 403.

Resolution actions are lowercase `approve`, `edit`, and `reject` on HTTP input. `edit` requires a
reason plus a complete replacement argument object. The frontend must clone the complete
`proposed_arguments` object and allow changes only to `editable_fields`. Frozen v1.1 additionally
limits edit to decreasing `knowledge_search.top_k` or `database_query.row_limit`; the other tools
have no editable fields. `reject` requires a reason. `approve` may include a reason but cannot
include edited arguments.

## 8. Artifact contract

Artifact list metadata is:

```text
artifact_id, task_id, format (PDF | JSON), filename,
media_type, checksum, size_bytes, created_at
```

Task submission has a related safe artifact reference with domain `type` instead of `format`.
Storage `location` and server filesystem paths are intentionally absent. PDF/JSON identification
uses `format` and `media_type`. Downloads must always use the existing guarded endpoint.

## 9. Identity and tenant boundary

### Development

`DemoIdentityProvider` ignores browser identity headers and creates a caller from server settings.
The Local Enterprise Compose defaults to configured user `U-LOCAL-ENTERPRISE`, tenant
`TENANT-DEMO`, and demo roles. Demo identity is rejected when `APP_ENV=production`.

The current public API does not expose a `GET /identity` endpoint, so the UI cannot reliably show
the configured demo user/tenant without inventing a value. It may state that the server manages
identity, but it must not claim a particular identity from frontend configuration.

### Production

`TrustedHeaderIdentityProvider` verifies one short-lived HMAC assertion covering user, tenant,
roles, scopes, supplier IDs, purpose, and timestamp. An approved gateway must create this
assertion. Browser task payloads are never an identity source. Nginx forwards request headers but
does not mint or validate the identity. The frontend must not store credentials in local storage,
put tokens in URLs, or let a user select a tenant/role.

## 10. Nginx

The existing unprivileged Nginx listens on 8080 and:

- serves `/usr/share/nginx/html`;
- uses `try_files $uri $uri/ /index.html`, already suitable for React Router deep links;
- handles `/api/` before the SPA fallback and strips the `/api` prefix via
  `proxy_pass http://copilot-api:8000/`;
- sets bounded proxy connection/read timeouts;
- sets `nosniff`, frame denial, no-referrer, and a self-only Content Security Policy;
- exposes a frontend-container `/health` response.

The same-origin design should be retained. The React production build only changes the static
assets copied into the image.

## 11. Docker and Compose

| Topology | Existing frontend state |
|---|---|
| `docker-compose.local-enterprise.yml` | Has `frontend`, builds `frontend/Dockerfile`, binds `127.0.0.1:8080`, waits for healthy API, has no environment/credentials, and joins only `enterprise-edge` |
| `docker-compose.yml` | Development/demo backend stack only; exposes API and dependencies directly; no frontend service |
| `docker-compose.production.yml` | Backend/RAG/PostgreSQL topology only; no frontend service or edge gateway |

The Local Enterprise isolation contract is tested: only the frontend publishes a host port, the
frontend cannot join the backend dependency network, and Nginx contains no backend credentials.
The current frontend Dockerfile is minimal and non-root but has no build stage because the legacy
UI has no build system. React requires a deterministic Node build stage followed by the existing
unprivileged Nginx runtime.

Production uses signed trusted headers but currently lacks the external gateway/front-door layer
that would mint them. Adding a standalone frontend container to that file without specifying the
trusted gateway would create an unusable or misleading authentication path. The production
deployment document should therefore describe the frontend image as a gateway-behind component;
the local enterprise Compose remains the complete runnable browser topology.

## 12. Tests and CI

Existing tests cover the API schema, task submission/read/cancel, minimized evidence, governed
artifact download, approval approve/edit/reject and conflicts, cross-user/tenant denial, recovery,
and Local Enterprise network isolation. The existing browser UI has no test coverage.

CI currently runs Python Ruff, formatting, mypy, unit/integration/contract/smoke/security suites,
PostgreSQL integration, Compose rendering, and the backend container build. It does not install
Node, validate a generated API client, lint/typecheck/build the frontend, run component tests, run
Playwright, or build the frontend image.

## 13. Migration decisions and gaps

1. Replace the unbundled production UI only after equivalent task, readiness, step, evidence, and
   artifact behavior exists in React and tests pass. Do not retain two competing production UIs.
2. Preserve the same-origin `/api` boundary and governed Artifact download.
3. Generate TypeScript types/client from a deterministic checked-in OpenAPI snapshot; generated
   output is never hand-edited.
4. Add a minimal `GET /v1/tasks` read API because task history cannot be implemented through an
   existing service. It must be tenant- and owner-scoped, newest-first, status-filterable, and
   bounded with cursor/offset pagination, with contract and integration tests.
5. Do not expand the Evidence or Approval API merely to match example UI fields. Missing public
   fields are documented limitations and remain hidden.
6. Do not add a frontend identity selector or a second authentication backend.
7. Prefer polling over WebSocket because reads are idempotent, normal execution is synchronous,
   and the frozen architecture has no event-stream contract.
