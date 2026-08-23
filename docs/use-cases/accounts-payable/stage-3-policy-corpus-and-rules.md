# Stage 3 Controlled AP Policy Corpus and Rule Manifest Report

**Status:** `COMPLETE — POLICY FOUNDATION ONLY`

**Policy profile:** `accounts_payable_policy.v1`

**Rule set:** `ap_rules.2026.1`

**Date:** 2026-08-23

## Delivered boundary

Stage 3 adds the controlled, synthetic Accounts Payable policy package and its deterministic rule
bindings. It does not register AP tool profiles, enable AP planning or execution, query business
data, run analytics, or generate an AP report. The Stage 1 domain manifest remains deny-by-default
with `execution_enabled=false`.

The committed fixture bundle under `data/policies/accounts_payable/v1` contains exactly four
sanitized policy documents, eight controlled chunks, one typed five-rule manifest and a validation
report. The documents contain no real supplier, employee, bank, tax, or payment-reference data.

## Contracts and consistency gates

The implementation adds immutable typed contracts for:

- the four frozen AP policy document families;
- the five frozen executable rule kinds;
- currency amounts, effective dates, approvals, document metadata and exact chunk bindings;
- the tenant-bound corpus manifest and immutable publication snapshot.

`load_ap_policy_bundle` fails closed unless all of the following hold:

1. corpus and rule JSON satisfy their exact Pydantic schemas;
2. tenant, namespace, approved collection and policy profile agree;
3. declared corpus, rule, document and excerpt SHA-256 checksums match;
4. the four document families and five rule kinds occur exactly once;
5. every rule resolves to the declared document ID, version, page, chunk, document checksum and
   excerpt checksum;
6. rule effective dates are contained by both the rule-set and bound document effective periods;
7. the controlled fixtures pass the deterministic unsafe-instruction content gate.

The executable manifest remains separate from retrieved prose. A document cannot select a tool,
change a threshold, grant authority or alter publication behavior.

## Atomic publication and retention

`enterprise-copilot-publish-ap-policy` (or `scripts/publish_ap_policy.py`) validates the entire
bundle before it writes anything. Publication writes a RAG contract JSONL payload, the normalized
rule manifest and snapshot metadata into a staging directory, atomically renames the completed
snapshot, and then atomically replaces only the tenant's `current.json` pointer.

Snapshot identity binds tenant, namespace, collection, index revision, corpus checksum, rule
manifest checksum and RAG payload checksum. Repeating the same generation is idempotent. Publishing
a new index revision creates a new immutable directory and retains the prior snapshot for tasks
that already reference it. The default publication directory is generated local state and is not
version controlled.

This command creates a validated ingestion payload and local immutable publication boundary. It
does not assume or bypass a provider-specific RAG ingestion API; deployment-specific transfer to
an approved RAG service remains an adapter/operations concern and does not weaken these gates.

## Reviewed fixture identity

| Property | Value |
|---|---|
| tenant | `TENANT-DEMO` |
| namespace | `tenant/TENANT-DEMO/finance/accounts-payable/v1` |
| collection | `accounts-payable-policy-v1` |
| documents / chunks | `4 / 8` |
| executable bindings | `5` |
| corpus checksum | `sha256:b8856e48c279338525f847f34df227d3e669fa492cdeb3ad2bebdb1a067ba02f` |
| rule manifest checksum | `sha256:3095ebb099a2db12dffbc699cf1f65bb7d8e324d025eb701af4bf825d6adab33` |
| RAG payload checksum | `sha256:2c1045677d53c7528246bae144430c7797f7752fc1270cc95f2ef42d1d5d9fbf` |

## Verification matrix

| Gate | Coverage |
|---|---|
| schema and checksum | exact contract validation plus corpus/rule/document/chunk drift rejection |
| effective dates | in-range rule resolution and out-of-range no-fallback failure |
| binding consistency | missing chunk and stale document version fail `POLICY_RULE_BINDING_MISMATCH` |
| tenant isolation | expected tenant cannot be remapped into another namespace |
| malicious document | checksum-aware injection mutation fails `POLICY_DOCUMENT_UNSAFE_CONTENT` |
| atomicity | no partial snapshot is visible before staging rename; current pointer uses replace |
| re-index retention | new index revision retains the old immutable snapshot |
| command smoke | validation/publication emits bounded JSON metadata and no policy body or secret |

Stage 4 may consume this policy foundation while adding only the five frozen AP database query
templates. It must not enable AP analytics or the complete UC2 workflow early.
