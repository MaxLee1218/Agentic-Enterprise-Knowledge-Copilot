# Security and Governance

## 1. Default posture

UC2 is deny-by-default and read-only. Its only permitted verbs are analysis, detection, comparison
and internal report generation. Approval authorizes access to a precisely bound controlled read;
it never becomes business approval of an invoice, PO or payment.

## 2. Permissions and data views

| Action | `finance_analyst` | `finance_approver` | `finance_auditor` |
|---|---:|---:|---:|
| submit AP task | yes, within scope | yes, within scope | no |
| execute approved read tools | yes | yes | no |
| view aggregate task/Evidence | own/assigned | own/assigned | assigned |
| view detailed amounts/identifiers | requires `finance:ap.detail` | requires same scope | explicit detail grant only |
| download report | requires `finance:ap.artifact:download` | same | assigned + download scope |
| resolve AP access approval | no | yes, same tenant and approved role | no |

Every access also requires tenant, purpose `accounts_payable_analysis.v1`, legal-entity,
business-unit, supplier and data-classification scope. Unknown role/purpose/template/field is
denied. The existing owner/tenant Artifact authorization remains mandatory.

## 3. Approval policy

Automatic execution is allowed when the user has detailed AP scope, all requested dimensions are
within pre-authorized scope, the range is at most 366 days, supplier count is at most 100, the
estimated invoice population is at most 50,000, no RESTRICTED field is requested, and all actions
are the four approved read-only capabilities.

Human `finance_approver` approval is required for a policy-designated cross-business-unit review,
a preflight estimate above the organization's routine-detail threshold, or a RESTRICTED policy
corpus. The absolute hard limits still apply after approval. A request for bank/IBAN/SWIFT/tax-ID,
raw SQL, an unregistered table, cross-tenant data, payment execution or any write is forbidden and
cannot be approved.

Existing approval binding remains: tenant/task/plan/step/tool/version/schema fingerprint,
controlled scope, expiry, complete proposed/resolved arguments and action fingerprint. AP v1
retains only the current tightening edits: lower `top_k` or `row_limit`. Scope, threshold, rule
version, template, operation, currency or output detail cannot be edited through Approval.

## 4. Financial data classification

The existing `PUBLIC | INTERNAL | CONFIDENTIAL | RESTRICTED | SECRET` scale is reused.

| Field | Classification | Report behavior |
|---|---|---|
| supplier name | INTERNAL | allowed within authorized scope |
| invoice/PO number | CONFIDENTIAL | masked in detail report; absent from aggregate report |
| invoice/PO/payment amount and currency | CONFIDENTIAL | allowed in scoped report; never logs/tags |
| invoice/due/payment date | CONFIDENTIAL | allowed when needed; never high-cardinality logs |
| payment reference / internal account number | RESTRICTED | not selected or persisted in v1 |
| bank account / IBAN / SWIFT | RESTRICTED | forbidden from tool profile and report; attempted access denied |
| tax ID | RESTRICTED | forbidden from v1 tool profile/report |
| personal contact information | CONFIDENTIAL | not selected; shared registry masks if encountered |
| credentials/tokens/passwords | SECRET | block output and task completion |

Implementation extends `SensitiveDataRegistry` aliases with `swift`, `tax_id`,
`payment_reference`, and `internal_account_number`. Bank/IBAN retains last four only in an
exceptional generic sanitizer path, but AP v1 Report Policy is stricter: remove/block the field
because it has no business purpose.

## 5. Threat model

| Threat | Control and required test |
|---|---|
| cross-tenant finance access | explicit tenant columns, composite FKs, repository/tool filters, cross-tenant tests |
| unauthorized supplier/entity/unit | trusted scope intersection, template predicates, direct Executor denial tests |
| invoice/amount leakage | detail/aggregate view policy, Evidence minimization, report Output Guard |
| bank-account leakage | column denylist, sensitive registry, report block, malicious fixture |
| prompt injection in invoice description | description is not selected in v1; all DB output remains untrusted data |
| malicious supplier name | structured field sanitization and Output Guard; no instruction authority |
| malicious policy document | retrieved text isolated; rule manifest is separate, signed/checksummed and deterministic |
| threshold manipulation | tightening-only user threshold merge; rule manifest and policy binding verification |
| raw SQL request | template-only contract, SQLAlchemy Select and AST allowlist |
| write-operation request | no registered capability; policy and Safety Verifier deny |
| approval bypass/replay | exact final fingerprint, expiry, CAS decision, checkpoint reauthorization |
| report leakage | tenant/owner/assignment/download checks, classification mode, checksum and audit |
| artifact cross-tenant download | task/artifact/tenant binding and not-found/denied behavior |

Invoice numbers and supplier names are data even when they contain strings such as “ignore policy.”
They never enter system instructions, manifest selection, SQL structure, rule resolution or
approval decisions.

## 6. Policy-as-knowledge versus policy-as-code

RAG is authoritative for the approved policy wording, scope, ownership, exceptions and citations.
The controlled rule manifest is authoritative for executable thresholds, applicability and
versions. Analytics is authoritative for calculations. The Verifier proves all three agree.

Change procedure:

1. policy owner approves a new document version;
2. rule owner updates the machine-readable manifest and bindings;
3. schema, checksum and effective-date validation runs;
4. RAG ingests documents under a new immutable snapshot;
5. a consistency gate resolves every rule binding to the snapshot;
6. tests/evaluation approve the rule-set version;
7. deployment activates both together; existing tasks retain their old snapshots.

Changing only a PDF or only a threshold cannot activate a new rule. A mismatch fails closed and is
audited.

## 7. Audit and observability

Audit captures requester, authenticated tenant/purpose, resolved scope hashes, time range,
contract/plan/profile versions, templates, rule set and rule IDs, policy and approval decisions,
tool outcomes, Evidence/Artifact IDs, verification and Artifact access/download. It does not copy
raw financial result sets, bank data, credentials, full payment references, unrestricted document
text or raw SQL.

Structured logs use safe low-cardinality identifiers and counts. `invoice_number`, `po_number`,
supplier name, monetary values and exception record keys are not metrics labels or log tags.

Clarification responses are governed input, not chat authority. Only the Task owner with current
`finance_analyst` permission may respond. The API rechecks tenant, task type, purpose, role, and
dimension scope, and selectable legal entities are exactly the current trusted identity's allowed
values. Answers, invoice identifiers, dates, and free-form response text are not ordinary log or
metric fields. Audit records store interaction IDs, round, requested field names, actor, outcome,
and safe error codes. Worker resume accepts only the API-created refreshed context bound to the
submitted record and checkpoint generation; model output cannot authorize scope.

## 8. Forbidden operations

The Registry and permission matrix must reject pay/cancel/reverse payment, approve/edit invoice,
create/edit PO, change supplier master/bank data, send bank instructions, email/publish a report,
arbitrary SQL/Python, or external financial integration. Adding any such action is a separate
high-risk use case with new contracts, policy, approval, side-effect, recovery and audit design;
this AP analysis approval cannot authorize it.
