# ADR-010: Version-bound Policy Knowledge and Deterministic Rules

## Status

Accepted

## Date

2026-08-22

## Context

AP exceptions depend on controlled thresholds such as required-PO amounts, PO variance tolerance,
materiality, early-payment days and overpayment tolerance. Allowing an LLM to read a policy PDF,
extract a number and calculate compliance is not reproducible. Hardcoding thresholds without a
document binding creates a different failure: policy text can change while code silently keeps an
old rule.

## Decision

- Store executable AP v1 rules in a schema-validated, versioned, checksummed controlled rule
  manifest, initially `ap_rules.2026.1`.
- Bind every active rule to tenant, applicability/effective dates and exact policy document ID,
  version, chunk/page and excerpt checksum.
- Use RAG Document Evidence for policy wording, scope, ownership, exceptions and citations. Use
  the controlled manifest for executable values. Use deterministic Analytics for all calculations.
- Publish a new policy snapshot and rule manifest only after an atomic consistency gate resolves
  every binding. A Task freezes both snapshots and fails closed on unavailable, stale or mismatched
  bindings.
- Permit a user to request a stricter materiality threshold but never a looser one. Record policy,
  requested and effective values in Calculation Evidence.
- Require report policy claims to cite Document Evidence and policy-governed exception claims to
  trace through Calculation/Database Evidence and the bound Document Evidence.

## Alternatives Considered

Prompt-only policy interpretation was rejected for nondeterminism and prompt-injection risk.
Unbound source-code constants were rejected for audit drift. Reading thresholds from unrestricted
operational tables was rejected because the business Database Tool is an analytical read boundary,
not a policy-authoring system. A general runtime rule engine was rejected as unnecessary; v1 uses
typed rule kinds and deterministic functions.

## Consequences

Policy publication requires coordinated document and manifest ownership, versioning, ingestion
and tests. Tasks may fail when governance artifacts are incomplete, which is the intended safe
behavior. Historical tasks can explain exactly which policy/rule versions classified a record.
Changing a threshold creates a new rule version and evaluation baseline impact rather than a
silent configuration edit.

## Related Documents

- [Domain model](../use-cases/accounts-payable/domain-model.md)
- [Analytics design](../use-cases/accounts-payable/analytics-design.md)
- [Evidence and verification](../use-cases/accounts-payable/evidence-and-verification.md)
- [Security and governance](../use-cases/accounts-payable/security-and-governance.md)
