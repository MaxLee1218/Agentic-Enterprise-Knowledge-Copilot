# Enterprise Agent Execution Console Architecture

**Status:** implementation design  
**Date:** 2026-08-13

## 1. Purpose

The frontend is an enterprise execution console for the implemented Supplier Quality Analysis
workflow. Its primary hierarchy is Task, Status, Execution, Approval, Evidence, and Artifact. It is
not a general chat client, an alternative workflow engine, or an authorization boundary.

The backend remains authoritative for lifecycle transitions, permissions, tenant/user ownership,
approval validation, evidence minimization, Artifact publication, and downloads.

## 2. Runtime topology

```text
Browser
  -> one frontend origin
  -> Nginx
       /          -> React SPA assets
       /api/*     -> FastAPI (prefix stripped)
  -> existing service / policy / repository / agent boundaries
```

Local Vite development uses the same application URLs. Vite proxies `/api` to the configurable
development target, defaulting to `http://127.0.0.1:8000`; feature code never contains that host.

No frontend code directly accesses PostgreSQL, the business database, RAG, Artifact storage, or
model providers. No Node server is deployed at runtime.

## 3. Technology decisions

| Concern | Choice | Reason |
|---|---|---|
| UI runtime | React 19 + TypeScript + Vite | Small static SPA, strict typing, fast local/test builds |
| Routing | React Router | Deep-linkable task, evidence, report, approval, and system views |
| Server state | TanStack Query | Request deduplication, mutation invalidation, bounded polling |
| Forms | React Hook Form + Zod | Accessible field errors and deterministic client validation |
| API generation | `openapi-typescript` + `openapi-fetch` | Generated types plus a typed Fetch client without Axios |
| Unit/component tests | Vitest + Testing Library + user-event | Browser-like component behavior without the backend |
| HTTP mocks | MSW | One contract-shaped mock boundary shared by component tests |
| E2E | Playwright | Real browser navigation, proxy, download, and workflow coverage |
| UI components | Small repository-owned primitives | No existing design system; avoids a second large styling contract |
| Styling | Plain modular application CSS with design tokens | Minimal dependencies and straightforward Nginx CSP compatibility |

TanStack Query owns remote data. React state owns only ephemeral presentation state such as open
dialogs and selected approval action. No Redux/Zustand store and no competing request wrapper are
introduced.

## 4. Source structure

```text
frontend/
├── openapi/
│   └── openapi.json              # deterministic FastAPI snapshot
├── src/
│   ├── api/
│   │   ├── client.ts             # openapi-fetch instance and normalized errors
│   │   ├── types.ts              # aliases of generated public contracts
│   │   └── generated/
│   │       └── schema.d.ts        # generated; never manually edited
│   ├── app/
│   │   ├── App.tsx
│   │   └── queryClient.ts
│   ├── components/               # AppShell, badges, states, dialog, metadata
│   ├── features/tasks/            # typed API operations and query hooks
│   ├── pages/
│   ├── test/
│   ├── utils/
│   ├── main.tsx
│   └── styles.css
├── tests/
│   └── e2e/
├── Dockerfile
├── nginx.conf
├── package.json
├── package-lock.json
├── tsconfig*.json
├── vite.config.ts
├── vitest.config.ts
└── playwright.config.ts

scripts/
└── export_frontend_openapi.py     # creates the snapshot from create_app().openapi()
```

Feature folders own query hooks and domain-specific presentation. Pages compose features but do
not issue raw Fetch calls. Generated code is isolated under `src/api/generated`.

## 5. Routes and information architecture

| URL | View |
|---|---|
| `/` | Redirect to `/tasks` |
| `/tasks` | Paginated current-user task history |
| `/tasks/new` | Simple enterprise task form with optional advanced constraints |
| `/tasks/:taskId` | Overview and execution steps |
| `/tasks/:taskId/evidence` | Type-specific minimized Evidence and lineage |
| `/tasks/:taskId/report` | Governed Artifact metadata and downloads |
| `/tasks/:taskId/approvals/:approvalId` | Authorized approval workbench |
| `/system` | Actual liveness/readiness and returned dependency states |

Task subnavigation uses real URLs rather than local tabs so refresh, bookmarking, and audit handoff
remain reliable.

## 6. Query and polling model

Stable query keys are:

```text
["tasks", filters]
["task", taskId]
["steps", taskId]
["evidence", taskId]
["artifacts", taskId]
["approval", taskId, approvalId]
["health"]
```

The task query controls polling:

- `CREATED`, `UNDERSTANDING`, `PLANNING`, `EXECUTING`, `RETRYING`, `REPLANNING`, and `VERIFYING`:
  every 2 seconds while the view is mounted;
- `WAITING_APPROVAL`: every 10 seconds;
- `COMPLETED`, `FAILED`, and `CANCELLED`: no polling.

Steps follow the same task-aware interval while the overview is mounted. Evidence and Artifacts
refresh when lifecycle state changes and on relevant mutations rather than on independent
high-frequency timers. Query polling automatically stops when the page unmounts.

Mutations are create task, cancel task, and resolve approval. Each invalidates only its task,
steps, evidence, artifacts, approval, and task-history keys.

## 7. API type lifecycle

The backend Pydantic/OpenAPI document is the source of truth:

```text
FastAPI/Pydantic
  -> scripts/export_frontend_openapi.py
  -> frontend/openapi/openapi.json
  -> openapi-typescript
  -> frontend/src/api/generated/schema.d.ts
  -> openapi-fetch and React query hooks
```

Commands:

```text
npm run api:export
npm run api:generate
npm run api:check
```

`api:check` regenerates both artifacts and fails on a Git diff in CI. The generated declaration
file is committed for review and deterministic `npm ci` builds. It is never manually patched.

Binary Artifact download is a normal browser navigation to the guarded same-origin API URL; it is
not routed through the JSON generated client.

## 8. Task history API boundary

The pre-migration audit found no list operation. The migration adds a minimal read-only endpoint
at `GET /v1/tasks` using the existing task service and repository boundaries. It:

- constrain every query by trusted tenant and creating user;
- require the existing task read permission;
- order newest first with task ID as deterministic tie-breaker;
- accept an optional real `TaskStatus` filter;
- use bounded `limit` and non-negative `offset` parameters;
- return `items`, `total`, `limit`, and `offset`;
- return the existing safe `TaskResponse` shape for items;
- never expose raw request metadata, task contract, plan input, tool input, or persistence JSON.

This is a backwards-compatible read surface. It does not change the frozen lifecycle or business
behavior.

## 9. Approval workbench

The page first obtains authorized `ApprovalDetailResponse`. A 403 produces an explicit read-only
authorization message; it does not infer or request a role from the user.

- Approve sends only `action` and optional reason.
- Reject requires a non-empty reason.
- Edit starts from a deep clone of the complete `proposed_arguments`; only fields returned in
  `editable_fields` get controls. The v1.1 controls support only integer `top_k` and `row_limit`
  decreases. The full cloned object is sent as `edited_arguments`.
- Resolved/expired approval actions are disabled. Backend 409 remains authoritative for races.

Requested arguments are rendered as read-only structured data. Sensitive credentials are not
expected in the contract and are never persisted by frontend code.

## 10. Evidence and Artifact presentation

Evidence cards render only public fields. Each type receives a distinct label and relevant
metadata, while common step/producer/time/lineage fields remain visible. Missing optional metadata
is described as not exposed rather than shown as false or empty business data.

Artifact cards show format, media type, filename, size, checksum, and creation time. Download URLs
use encoded task and Artifact IDs under `/api/v1/tasks/.../artifacts/...`. Storage locations are
never accepted from API data or constructed by the UI.

## 11. Error and accessibility model

The API adapter normalizes the public error object and FastAPI's nested 401 `detail` shape into one
`ApiError` carrying status, code, safe message, task ID, trace ID, and details. Pages map 401, 403,
404, 409, 422, 500, 503, network failures, and timeouts to actionable user-facing states. Raw
response bodies, stack traces, and request arguments are not logged or rendered.

Semantic headings, landmarks, forms, tables/lists, labels, error associations, keyboard focus,
and modal focus behavior are required. Status components always include text/iconography and never
depend on color alone. Motion is limited and respects reduced-motion preferences.

## 12. Build and deployment

The frontend image is multi-stage:

1. pinned Node Alpine build with `npm ci` and `npm run build`;
2. existing unprivileged Nginx runtime serving `dist/`.

The Nginx `/api/` location remains ahead of `try_files`; all application routes fall back to
`index.html`. The final image contains no source dependencies, package manager, credentials, or
backend configuration.

Local Enterprise Compose continues to expose only `127.0.0.1:8080`. Production deployment places
the frontend behind the approved identity gateway; Nginx does not become an authentication
service.

## 13. Legacy migration

The existing visual tokens, same-origin request pattern, readiness semantics, task submission,
step/evidence summaries, and governed downloads are retained in behavior. The former `app.js` and
standalone `styles.css` implementation have been replaced by typed React features and removed, so
only one production UI remains.
