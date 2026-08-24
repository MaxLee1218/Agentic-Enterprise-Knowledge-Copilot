# Stage 11 — Full local enterprise E2E

**Status:** `COMPLETE — 2026-08-24`  
**Frozen baseline:** Accounts Payable Invoice Compliance & Exception Investigation v1, design `1.0`  
**Accepted topology:** isolated Compose project `copilot-stage11-e2e`  
**Production readiness:** `NOT CLAIMED`

## Delivered boundary

Stage 11 proves both frozen vertical slices through the browser-facing Local Enterprise topology.
The accepted run created new project-scoped volumes instead of deleting any pre-existing local
Compose data. It ran Copilot migrations separately from the business migration/seed sequence,
loaded the reviewed Supplier Quality seed, additively loaded the AP v1 seed, published the
checksum-bound AP policy snapshot, ingested the five controlled Supplier Quality PDFs into the
formal Enterprise RAG image, and then started the frontend, API, PostgreSQL and RAG services.

The AP knowledge path remains the Stage 3 controlled publisher and exact in-process retrieval
adapter. Its immutable snapshot is mounted read-only into the API; it is not represented as an
external Enterprise RAG HTTP service. Supplier Quality continues to use the independently packaged
Enterprise RAG HTTP service. Both knowledge paths remain backend-only.

The accepted Copilot understanding/planning provider was `controlled-local-mock`. Formal Supplier
retrieval, PostgreSQL reads, policies, tools, Evidence, analytics, reporting, verification,
persistence, checkpointing and browser behavior were real local services. The RAG grounded
generation boundary used the backend-only local generation stub. No external model path or data
egress is claimed by this report.

## Fresh-volume and controlled-data evidence

| Boundary | Accepted evidence |
|---|---|
| Copilot PostgreSQL | migrations through `20260812_0004`; LangGraph saver initialized |
| Supplier Quality seed | 17 suppliers, 2 tenants, 5,000 inspections; checksum `7afe142367bd8a69d3051d90b5dca694545cf7b62da4a56c8a60e9e06bc07bd4` |
| AP additive seed | 3 legal entities, 4 business units, 24 POs, 27 invoices, 11 payments; checksum `e920b4b13403831b0c4e7150edea452736f5c278cb2ed272b98c25da66b02f91` |
| AP tenant control | invoice counts `27 total / 25 TENANT-DEMO / 2 TENANT-A` |
| AP policy snapshot | `ap-policy-c923cd8947a553554aa14951`; 4 documents, 8 chunks, 5 rule bindings |
| AP rule manifest | `sha256:3095ebb099a2db12dffbc699cf1f65bb7d8e324d025eb701af4bf825d6adab33` |
| Supplier formal RAG | image `enterprise-rag-engine:local`, image ID `sha256:85f01ff5438388fd591dc81cc4d1a6ac3b35fd0d65c59b5d20a5fe75b5b4b1d8` |
| Supplier RAG ingestion | 5 PDFs, 81 chunks created and stored in `supplier_quality_demo` |

The runtime database role successfully executed the reviewed `SELECT` probes and was denied
`INSERT`, `UPDATE`, `DELETE` and `CREATE TABLE`. Copilot persistence tables were absent from the
business database, and Supplier/AP business tables were absent from Copilot PostgreSQL.

## Accepted task and Artifact evidence

The safe machine-readable run manifest is
[the Stage 11 E2E report](../../../evaluation/reports/accounts-payable-stage11/2026-08-24/report.json).
It records base revision `41be9c2c395c517b0c137a467ccc4fbc5b2a700f`, dirty-tree status, Task,
Trace, Artifact and checksum identifiers without business payloads or secrets.

| Scenario | Task / Trace | Artifact / checksum |
|---|---|---|
| Supplier Quality JSON | `T-c0ba4ab58a314dfea792948966c7529f` / `TRACE-333a50f54901446482ec0784e2c2e34c` | `A-63bf367308f14ed591e26c609fe9fd52` / `sha256:69613d5ed2a39b8207e7d79165167fa5783ec50f750da64848b9a670f6222678` |
| Supplier Quality PDF | `T-cc0f49cc574c4db08779c3a066e13acd` / `TRACE-85d398de621d4ade81d9cad1480d4e74` | `A-8b7e6bba537d4a52bf977a3165b39f0e` / `sha256:31aa1ed26a211eb775af68e7efe40179d7b890dc706ab809bfa6078881aeebb2` |
| AP clean JSON | `T-ba08cb3a03bd4175970f834bb0358ad1` / `TRACE-dc3892358790406eae75ab2a28d6880b` | `A-c3dbdf4e60cc41e38afe7bfda9e7ba01` / `sha256:d88aeb8b28e234efc99baf79dc25fb55636ae05bd938a8e62a06253985196d5d` |
| AP mixed JSON | `T-160e4d5837584a35b2f9489617c4b0e5` / `TRACE-521fe91a9c7b4518a151c4f1923835d4` | `A-992d56c7bc544db0a434e670580c6928` / `sha256:0ae03558cd1016321cdecfd022c63611e6361202d6ba30850331e8f0aa254979` |
| AP mixed PDF | `T-0979a273e3e84cf7905ca565bcabfeda` / `TRACE-97ed5904fd014be09051506856ba41b5` | `A-1ca99f27656f4ec3a4bfb91ec0b28ac6` / `sha256:41ba821d8bbc9f4755c28e51e51bd6af44c975ea66dde6f719f581e6fccc7c63` |
| AP approval restart | `T-50d7bdbe21924b89abe59cbc6af17dab` / `TRACE-7425676aea294805bfdee19a008ae522` | `A-68f675d5b84d4aa29b92c2164330e6d7` / `sha256:2c169d757f881126dcfdafecd6cde7db859a84b4d42ccbcf36767682918a48e5` |

The clean AP control is the single settled `2026-06-01 / LE-US-01` invoice: one invoice and zero
exceptions. The mixed Q2 control produced 23 invoices, 7 unique exception invoices, rate
`0.30434783`, 5 findings and 2 warnings, including the exact six-type count oracle. Every AP task
used the frozen 14-step plan: 1 policy retrieval, 5 database templates, 7 analytics operations and
1 report step.

The approval case checkpointed the completed knowledge step, entered `WAITING_APPROVAL` on the
first database action with row limit 50,000, restarted the API, loaded the same pending approval,
and resumed to `COMPLETED` without replaying knowledge work. A later API restart, formal RAG
restart/replacement/outage, business database outage and full stack stop/start preserved the
accepted Tasks, Evidence, checkpoint state, Artifact metadata and exact downloadable bytes.

## Browser and safety acceptance

Real Google Chrome submitted a mixed AP JSON task through the console, displayed the AP badge,
14 steps and DOCUMENT/DATABASE/CALCULATION Evidence, opened the verified report summary, downloaded
the Artifact and recomputed its SHA-256. The accepted browser Task was
`T-2db4c2a1b2ab4c95832fbf238d97101a`, Trace
`TRACE-930c98a4280a41908761ce1f43b2f2cf`, Artifact
`A-579dea0e6c024a14ad5bd3a65dba3077`, checksum
`sha256:668edbd511a126aaab2a10ac83d37f2611ca5585e820c83b236bd405ba71d755`.

All AP downloads were checked for checksum integrity, restricted finance keys and configured
secret values. No bank account, IBAN, SWIFT, tax ID, payment reference, internal account number or
configured secret entered the accepted HTTP Artifact bytes. The frontend receives no database,
RAG or model credentials.

## Regression gates

- backend: `736 passed, 9 skipped`; the only first-run failures were sandbox-denied localhost MCP
  ports, and all three passed when localhost binding was enabled;
- Ruff lint and formatting: pass;
- strict mypy: pass across 446 source files;
- documentation and Compose contract/config checks: pass;
- frontend ESLint, Prettier, TypeScript and production Vite build: pass;
- frontend unit tests: 31/31 pass;
- full Playwright console regression: 6 pass, 1 opt-in live test skipped;
- opt-in live AP Chrome E2E: 1/1 pass;
- OpenAPI export and generated TypeScript snapshot: unchanged.

Two non-accepted diagnostic executions remain auditable. One external DeepSeek Supplier plan
returned `LLM_INVALID_RESPONSE_ERROR` before creating a plan or executing any tool; the external
planner path is therefore not an accepted Stage 11 claim. An initial Q1 AP control correctly
failed because its unpaid invoice had no eligible coverage across all required operations; the
accepted clean control uses the reviewed settled invoice instead. Neither diagnostic task produced
an Artifact.

## Deferred boundary

Stage 11 does not prove production ERP/SAP/MCP integration, external-model governance, production
identity, backup/restore, rollback, retention, performance under production load, threat sign-off,
high availability or operational readiness. Those remain Stage 12 or later work. Production
readiness is not claimed.
