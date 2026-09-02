# Frontend Development

The frontend is a React 19 + TypeScript enterprise execution console. It is a same-origin client
of the existing FastAPI contract; it does not connect to PostgreSQL, the business database, RAG,
LLM providers, or Artifact storage directly.

## Prerequisites

- Node.js 22.18 or a compatible Node 22 release;
- npm from the Node distribution;
- Python 3.11 or later with this repository installed for OpenAPI generation and browser E2E.

Install the locked dependency graph:

```bash
cd frontend
npm ci
```

`package-lock.json` is authoritative and must be committed with dependency changes. Do not commit
`node_modules`, `dist`, coverage, or Playwright result directories.

## Run locally

Start the API from the repository root:

```bash
source .venv/bin/activate
python -m copilot.persistence.migrate
uvicorn copilot.bootstrap.api:app --host 127.0.0.1 --port 8000
```

Then start Vite in another terminal:

```bash
cd frontend
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api/*` to `http://127.0.0.1:8000` and removes the
browser-only `/api` prefix. Set `VITE_API_PROXY_TARGET` only when the approved local API uses a
different origin.

The browser cannot choose or send a tenant, user, role, scope, supplier allowlist, database, or
tool. Development identity comes from server settings. Production identity comes from the
trusted upstream gateway and remains enforced by FastAPI.

## Checks

```bash
npm run typecheck
npm run lint
npm run format:check
npm run test
npm run build
```

Vitest component tests use MSW only at the HTTP boundary. They cover task validation and
submission, lifecycle and failure states, steps, Evidence, Artifacts, clarification questions,
typed controls, partial/free-form answers, stale conflicts, waiting cancellation, approval
authorization and conflicts, edit restrictions, rejection, and cancellation.

Install Playwright's browser once, then run the real E2E suite:

```bash
npx playwright install chromium
npm run test:e2e
```

Playwright starts a hermetic FastAPI application backed by the real Task Service, workflow graph,
Evidence ledger, Approval Service, Artifact Service, and offline deterministic adapters. It also
starts Vite, so the happy path verifies browser → `/api` proxy → FastAPI → Agent workflow →
Evidence → downloadable Artifact. Separate tests cover approval, rejection, cancellation, and a
typed failure response. A browser interaction test also exercises AP task creation, two
clarification rounds under one Task ID, resume, completion, and PDF presentation; backend
PostgreSQL recovery/concurrency remains covered by the backend integration suite.

## OpenAPI types

FastAPI/Pydantic is the source of truth. Regenerate the committed snapshot and TypeScript schema:

```bash
npm run api:generate
```

Check freshness without accepting changes:

```bash
npm run api:check
```

Review changes to both `openapi/openapi.json` and `src/api/generated/schema.d.ts`. Never hand-edit
the generated declaration.

## Production image

Build the multi-stage image from the repository root:

```bash
docker build -t enterprise-copilot-frontend:local frontend
```

The build stage runs `npm ci` and `npm run build`. The final unprivileged Nginx image contains only
the compiled SPA and Nginx configuration. `/api/` proxies to `copilot-api:8000`; all other unknown
paths fall back to `index.html` for React Router deep links.

Local Enterprise Compose exposes the console only on `127.0.0.1:8080`. In production, place this
endpoint behind the approved identity gateway; Nginx serves and proxies the application but does
not mint or validate identity assertions.
