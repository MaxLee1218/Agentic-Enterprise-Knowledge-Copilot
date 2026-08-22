# ADR-009: Multi-domain Contracts and Capability Manifests

## Status

Accepted

## Date

2026-08-22

## Context

The implemented Supplier Quality vertical slice has sound shared lifecycle, execution, evidence,
policy, persistence and API boundaries, but its Task Contract, Planner rules, tool schemas, input
construction, permissions, reports and verifier adapters encode a single domain. Adding Accounts
Payable by copying a Graph or Agent would duplicate governance. Replacing the current contracts
in place would make stored Supplier Quality Plans and checkpoints depend on the latest schema and
break historical recovery.

## Decision

- Keep one Task lifecycle, LangGraph, Planner infrastructure, Tool Registry/Executor, Evidence,
  Approval, Persistence, API and frontend foundation.
- Add a small code-owned, deny-by-default `DomainCapabilityManifestRegistry` keyed by versioned
  `TaskType`. A manifest selects the domain constraint model, understanding adapter, capability
  contract profiles, query templates, analytics operations, report profile, Plan rules, input
  builder, verifier profile, permission purpose and limits.
- Generalize `TaskContract.constraints` to a task-type-validated union. Existing serialized
  Supplier Quality contracts are `task-contract.v1`; AP uses `task-contract.v2` and
  `accounts_payable_analysis.v1`.
- Persist `tool_version` and `contract_profile` on new TaskSteps. Resolve a tool definition by
  stable name plus those versions. Upcast old steps only through an exact historical schema
  fingerprint; never bind them to an arbitrary latest definition.
- Keep stable capability names `knowledge_search`, `database_query`, `analysis_engine` and
  `report_generator`. Profiles extend those capabilities; they do not create Finance Agent tools.
- Keep business algorithms, query templates, report models and evaluation data domain-specific.

## Alternatives Considered

Copying a Finance Graph/Agent was rejected because it duplicates Policy, Approval, Executor,
Evidence, persistence and recovery. A universal dynamic rule/plugin framework was rejected as
unnecessary for two use cases. Replacing live ToolDefinitions without version binding was rejected
because old plan schema validation and checkpoint resume would become nondeterministic. Forcing AP
into `supplier_quality_analysis.v1` was rejected because it changes a frozen business meaning.

## Consequences

Shared code gains explicit profile selection and compatibility serializers, which increases
contract tests and registry complexity. In return, each Task remains reproducible against its
exact domain and tool contracts, Supplier Quality history stays readable, and later domains must
provide an explicit manifest rather than adding prompt-only behavior. A manifest is static
governance configuration, not user-extensible plugins.

## Related Documents

- [Accounts Payable architecture](../use-cases/accounts-payable/architecture.md)
- [Accounts Payable task contract](../use-cases/accounts-payable/task-contract.md)
- [Platform reuse audit](../use-cases/accounts-payable/platform-reuse-audit.md)
- [Frozen Supplier Quality baseline](../design/design_baseline.md)
