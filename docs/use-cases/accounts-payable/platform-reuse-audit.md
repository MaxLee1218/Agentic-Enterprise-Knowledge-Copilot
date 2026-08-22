# Current Platform Reuse Audit

## 1. Audit basis

This audit was performed against the repository on 2026-08-22, including `AGENTS.md`, the seven
frozen Supplier Quality design files, README/architecture/security/evaluation/API/frontend docs,
`pyproject.toml`, production modules, migrations, evaluation data and tests. The current working
tree was clean before these design documents were added.

Verified current behavior:

- only `supplier_quality_analysis.v1` exists in `TaskType` and Permission purpose checks;
- Task Understanding outputs goal, Supplier IDs, year/quarter, Supplier Quality metrics,
  Supplier report deliverable and missing information;
- missing information ends in recoverable `FAILED`; there is no clarification state;
- Planner sees Registry-derived tools, but prompts, fixed plan and runtime inputs require the
  four Supplier Quality tools and dependencies;
- Registry officially exposes the four stable capability names; optional MCP can import/export
  only through governed paths and does not broaden the business slice;
- Database supports `supplier_quality_summary_v1` and `supplier_quality_trend_v1` on
  `quality.v1` only;
- Analytics supports defect count, inspected count, defect rate and period trend only;
- Evidence types are DOCUMENT/DATABASE/CALCULATION with Calculation parent lineage;
- Verifier framework contains Artifact integrity, Evidence structure, Deliverable, Citation,
  Numeric and Safety checks;
- Report Tool actually supports one Supplier Quality report model rendered as JSON or PDF;
- approval can pause immediately before an exact controlled call and can only reduce `top_k` or
  `row_limit` through complete replacement arguments;
- workflow persistence stores versioned JSON contracts/plans/results plus tenant-scoped Evidence,
  Artifact, Approval, Audit, leases and checkpoints;
- API and React frontend share generic Task/step/Evidence/Artifact resources, but UI copy/examples
  and generated enums remain Supplier-specific;
- evaluation has 30 Supplier Quality cases and a Supplier-only harness/baseline;
- tenant isolation is implemented in workflow persistence and quality queries; roles/purpose/data
  access are hardcoded to the Quality domain.

## 2. Classification matrix

Legend: A = fully reusable, B = reusable with extension, C = Supplier-specific coupling requiring
minimal refactor, D = intentionally domain-specific.

| Module | Current state | Supplier coupling | AP reuse | Required change | Risk |
|---|---|---|---|---|---|
| Contracts/enums | Strong immutable Pydantic contracts | one TaskType, quality ArtifactType, quarter-only constraints | C | versioned constraint union, AP enums/artifacts, historical upcaster | High: persisted JSON/checkpoints |
| Task Intake | validates untrusted text and tightening-only options | artifact mapping and default purpose are quality-only | B/C | task-type-aware artifact mapping and trusted domain scope | Medium |
| Task Understanding | structured provider boundary and trusted-scope checks | Supplier/year/quarter/metrics/report schema and prompt | C | manifest-selected understanding schema/adapter; retain missing-info failure | High |
| Planner manifest | Registry-derived, permission-filterable | schema is general | A/B | filter by domain manifest/profile | Low |
| Fixed plan | deterministic four-step quality plan | fully Quality named/wired | D for SQ, C at selector | keep factory; add AP factory and manifest-selected factory registry | Medium |
| Plan Validator | generic DAG/capability/schema checks | `_supplier_quality_rules`; exact capability-set equality | B | domain rule-set selection, versioned profile resolution | High |
| Plan repair/replan | bounded, Contract preserving | prompt assumes current final report rules | B | render manifest plan rules into trusted prompt; deterministic validator authoritative | Medium |
| LangGraph | one explicit lifecycle with checkpoints | default identity/purpose and runtime task-type branches | B/C | select domain services from Contract; no graph copy | High |
| Step Input Builder | safe Contract/prior-Evidence construction | every input hardcoded to Quality templates/metrics/report | C | manifest-owned, domain-specific input builders behind one interface | High |
| Tool Registry | governed lifecycle, schema/risk/provenance/namespaces | local names default to four capabilities; one live definition/name | B | versioned profile lookup and historical schema binding | High |
| Tool Executor | context, auth, approval, idempotency, schema, Evidence, audit | none material beyond authorizer purpose | A/B | accept profile/version resolution; reuse execution path | Medium |
| Knowledge Tool | HTTP/mock adapter, retry, DOCUMENT Evidence | quality collection semantics supplied by input | A/B | AP collections/metadata and rule-binding validation | Medium |
| Database Tool | read-only SQLAlchemy templates, AST, scope, checksum | schema/template enum, purpose and access profiles are Quality-only | B/C | AP schema registry/templates/access profiles; preserve tool name | High |
| Business DB models/seed | Supplier and quality facts, deterministic seed | only quality entities; supplier reused | D + B | AP models and additive seed/migration; composite tenant FKs | High |
| Analytics Tool | deterministic, checksum-bound, CALCULATION Evidence | one Quality row schema and four metrics | B/C | strict operation union and AP operations; retain Quality adapter | High |
| Reporting Tool | strong model, JSON/PDF, atomic Artifact, Output Guard | model/composer/filename/artifact type all Quality-specific | B/C | report profile registry and AP model/composer/render sections | High |
| Evidence contracts/ledger | tenant-scoped immutable append/dedup/lineage | no material domain type coupling | A | profile metadata validators only | Low |
| Citation/lineage | type-compatible claim tracing | candidate mapper assumes Quality report | B | AP report-to-claim mapper and policy dual-lineage rule | Medium |
| Numeric Verifier | Decimal baseline comparison | baseline extractor assumes Quality `metrics` layout/precision | B | operation-aware metric adapter and money/currency units | High |
| Deliverable Verifier | generic structured sections | consumes current required section IDs | A/B | manifest supplies AP required sections | Low |
| Safety Verifier | registry/approval/read-only/table/field/sensitive checks | bootstrap provides one Quality schema allowlist | B | per-task domain allowlists and sensitive AP fields | High |
| Artifact Integrity | ownership/type/size/checksum/report parse | exactly one Quality Artifact/report parser | B | AP Artifact types/parser selected by profile | Medium |
| PermissionMatrix | centralized action deny-by-default | only quality roles, purpose and four-tool frozen set | C | domain role/purpose rules; tools remain shared | High |
| DataAccessPolicy | deny-by-default table/field profiles | only quality roles/templates/tables | C/D | add AP-specific profiles selected by purpose/template | High |
| Approval | exact action/schema/fingerprint/edit/CAS/resume | quality approver role and fixed plan ID at service call sites | B/C | manifest role and plan identity; keep binding semantics | High |
| Security guards | trust types, prompt isolation, redaction, Output Guard | field registry lacks several AP aliases | A/B | add AP aliases and stricter report disposition | Medium |
| Task Persistence | tenant-scoped JSON and immutable histories | deserializes concrete current TaskContract/Plan | B/C | versioned deserializer/upcaster, profile fields | High |
| Evidence/Artifact/Audit repositories | tenant-scoped, governed | no AP-specific tables | A | no second store; classification tests | Low |
| Checkpoint | LangGraph saver separate from authority | serialized state contains current contract/plan types | B | backward-compatible state decoding and profile resolution | High |
| API | generic `/v1/tasks` resources and safe errors | enum values and submission output mapping only | A/B | extend enums; no finance routes | Low/Medium |
| Frontend | generic Task history/timeline/Evidence/download/approval | examples, empty copy, report assumptions are Quality-specific | B | task-type badge/selector, generic copy, AP summary | Medium |
| Evaluation contracts/evaluators | strong general oracles and safety metrics | defaults and harness use quality purpose/mocks | B/C | dataset-selected domain harness and AP evaluators/metrics | High |
| Evaluation dataset/baseline | 30 deterministic Quality cases | fully Supplier Quality | D | new independent AP dataset/baseline; retain existing | Low |
| Unit/integration/security/E2E tests | broad Quality/runtime coverage | many exact four-step and Quality fixtures | A + D | shared-platform suite + UC1 regression + UC2 suite | High gate |
| Observability | correlation, redaction, metrics and audit | quality text in a few events only | A/B | safe task type/profile/rule fields; remove domain event prose | Low |
| Deployment/config | shared API/CLI/Docker/Postgres/RAG boundaries | seed/ingestion only Quality | B | additive AP seed/rule/RAG config; no new runtime | Medium |
| MCP | implemented optional governed boundary | frozen exported business allowlist remains Quality | A/no v1 use | no UC2 export; later explicit allowlist only | Low |

## 3. Reuse conclusions

### Fully reusable

Task state machine, LangGraph/checkpoint mechanism, Tool Executor control flow, Evidence Ledger,
Artifact/Audit repositories, API resource pattern, observability primitives and most verification
interfaces are the shared platform.

### Reusable with extension

Planner, Registry, Database/Analytics/Report capability infrastructure, policy/approval framework,
Verifier framework, frontend and evaluation engine remain single shared systems but need
task-type-selected profiles.

### Coupling that must be removed before AP execution

The blocking couplings are concrete Task/Artifact enums and constraints, Quality-only
understanding/prompt, fixed plan selection, hardcoded Step inputs, live-definition-only Registry
resolution, Quality-only permission/data access purpose, report/analytics schemas, verifier
candidate mapping, persistence decoding and evaluation harness defaults.

### Must remain domain-specific

Supplier defect formulae and plan/input/report adapters remain Supplier Quality-specific. AP
duplicate/variance/PO/terms/overpayment rules, AP tables/templates, AP report model and AP
evaluation oracles remain AP-specific. Sharing infrastructure does not merge their business
semantics.

## 4. Frozen-design conflict record

Supplier Quality v1.1 explicitly states that it is the only supported scenario and that new task
types/contracts/tools/metrics require a new design review. This UC2 work complies by preserving
`docs/design/` as historical authority and creating a separate versioned design plus Proposed
ADRs. Implementation is not authorized as a silent v1.1 change.

The repository README now accurately says optional MCP is implemented, while the older frozen
Supplier Quality baseline says MCP is outside that scenario. These statements refer to different
scopes and dates; UC2 neither depends on nor changes MCP.
