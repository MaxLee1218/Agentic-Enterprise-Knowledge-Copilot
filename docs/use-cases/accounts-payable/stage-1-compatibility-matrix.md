# Stage 1 Multi-domain Contract Compatibility Matrix

**Stage:** `1 — Multi-domain contracts and manifest routing`  
**Status:** `COMPLETE — 2026-08-22`  
**UC2 execution status:** `DISABLED`

## 1. Implemented boundary

Stage 1 adds the typed and persistence-compatible foundation needed to represent two domains while
preserving the Supplier Quality execution path. It does not add AP database tables, policy data,
query templates, calculations, report generation or an executable AP workflow.

The implementation includes:

- a task-type-validated `TaskContract` constraint union;
- `task-contract.v1` for Supplier Quality and `task-contract.v2` for AP;
- a required, bounded, non-authorizing business `goal` on new Contracts;
- six typed AP exception values, task-type-routed AP report Artifact values and bounded AP v1
  constraints;
- exact `tool_version` and `contract_profile` on every new `TaskStep`;
- a code-owned `DomainCapabilityManifestRegistry` with no fuzzy or latest-version fallback;
- trusted task-type/purpose binding at intake and Graph boundaries;
- manifest-selected understanding, plan, input and verifier profile identifiers;
- exact historical Supplier Quality Contract/Plan JSON upcasting at repository boundaries;
- legacy checkpoint upcasting for both JSON and msgpack serialization;
- fail-closed AP planning/input/execution gates.

## 2. Domain contract matrix

| Boundary | Supplier Quality | Accounts Payable |
|---|---|---|
| Task type / purpose | `supplier_quality_analysis.v1` | `accounts_payable_analysis.v1` |
| Contract schema | `task-contract.v1` | `task-contract.v2` |
| Constraints | existing `TaskConstraints` / `SupplierQualityConstraintsV1` alias | `AccountsPayableConstraintsV1` |
| Artifact types | `QUALITY_ANALYSIS_REPORT_PDF/JSON` | `ACCOUNTS_PAYABLE_REPORT_PDF/JSON` |
| Understanding profile | `supplier_quality_understanding.v1` | `accounts_payable_understanding.v1` |
| Plan profile | `supplier_quality_plan.v1` | `accounts_payable_plan.v1` |
| Input profile | `supplier_quality_inputs.v1` | `accounts_payable_inputs.v1` |
| Verifier profile | `supplier_quality_verifier.v1` | `accounts_payable_verifier.v1` |
| Execution enabled | yes, unchanged behavior | **no — denied with `DOMAIN_EXECUTION_NOT_ENABLED`** |

Both manifests require the same four stable capability names in frozen order. Neither prompt text
nor user metadata may choose a profile, capability, task type or permission purpose.

## 3. Capability profile matrix

| Capability | Supplier Quality profile | AP profile |
|---|---|---|
| `knowledge_search` | `supplier_quality_policy.v1` | `accounts_payable_policy.v1` |
| `database_query` | `supplier_quality_database.v1` | `accounts_payable_database.v1` |
| `analysis_engine` | `supplier_quality_analytics.v1` | `accounts_payable_analytics.v1` |
| `report_generator` | `supplier_quality_report.v1` | `accounts_payable_report.v1` |

The Tool Registry resolves a step only by `(tool_name, tool_version, contract_profile)`. The AP
profiles are intentionally not registered in Stage 1, so an AP Contract can be parsed and audited
but cannot reach a business tool.

## 4. Historical JSON and checkpoint behavior

| Stored payload | Read behavior | Write behavior |
|---|---|---|
| current versioned Contract | validate exact declared schema/domain pair | persist current JSON |
| old Supplier Quality Contract without schema version/goal | accept only the exact historical object shape; inject `task-contract.v1` and the canonical non-authorizing Quality goal | no bulk rewrite; a later normal save writes current JSON |
| current Plan with both step profile fields | validate both fields and exact Registry binding | persist unchanged |
| old Supplier Quality Plan with neither step profile field | accept only whitelisted full input/output Schema pair fingerprints; inject Quality profile and `legacy-schema-sha256:<digest>` | no bulk rewrite |
| partial/mixed profile fields | reject | none |
| unknown old Contract shape or tool Schema fingerprint | reject; never bind to latest | none |
| old LangGraph JSON/msgpack Pydantic values | run the same exact upcasters after allowlisted decoding | subsequent checkpoints use current models |

The recognized historical fingerprints cover the implemented production and offline Supplier
Quality v1 tool Schema pairs. A legacy alias resolves only when the active Registry definition has
the same complete Schema-pair digest and matching Quality profile; moving an old Plan into an
incompatible adapter environment fails closed.

## 5. Compatibility assertions

- The Supplier Quality state set, four capability names, formulas, tool schemas, approval
  semantics, retry/replan behavior, Evidence types and Artifact content are unchanged.
- `TaskConstraints` remains import-compatible; `SupplierQualityConstraintsV1` is an explicit alias,
  not a new serialized business meaning.
- No workflow table or bulk data migration is introduced.
- Existing API resources remain unchanged; enum schemas are additive.
- The shared Graph remains single-instance. Domain selection is trusted and deterministic.
- AP contracts cannot be planned, build step input, select a verifier or execute a tool until the
  later domain stages register and test each exact implementation profile.

## 6. Verification evidence

Stage 1 adds tests for:

- AP success and validation boundaries, including dates, currencies, duplicates, read-only and
  rule version;
- cross-domain constraints, Artifact and capability substitution;
- trusted purpose/task-type authorization;
- manifest unknown-domain and disabled-execution denial;
- exact Registry version/profile resolution;
- old Contract, Plan and checkpoint restoration;
- unknown, partial or tampered historical payload rejection;
- unchanged Supplier Quality planning, execution, approvals, persistence, API and tenant checks.

The exact executed commands and counts are recorded in the Stage 1 completion handoff, not frozen
as normative contract values in this matrix.
