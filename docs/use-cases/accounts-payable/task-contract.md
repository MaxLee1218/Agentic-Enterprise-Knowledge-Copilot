# Task Understanding, Task Contract and Planner Contract

## 1. Trust boundary

Task Understanding parses only business intent. It cannot create authority.

| Trusted server context | Untrusted natural-language candidate |
|---|---|
| `tenant_id`, `user_id`, roles, action scopes | goal and requested exception types |
| authorized supplier/legal-entity/business-unit IDs | requested suppliers/entities/units |
| data classification and detailed/aggregate access | time range and report preference |
| purpose and allowed task types | currency scope |
| server limits, read-only requirement, deadline | stricter materiality thresholds |
| policy-derived approval requirement | request to require extra approval |

Requested scope is intersected with trusted scope. A non-empty requested set containing an
unauthorized ID fails `SCOPE_VIOLATION`; it is not silently broadened or partially executed.
Natural-language `read_only=false`, role claims, threshold relaxation, raw SQL, or approval bypass
language has no contract effect.

## 2. Task Understanding candidate

The model-facing schema is a candidate, not the final contract:

```python
class APTaskUnderstandingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    goal: str = Field(min_length=1, max_length=2_000)
    task_type: Literal["accounts_payable_analysis.v1"]
    time_range: DateRangeCandidate  # start/end may be None
    requested_supplier_ids: tuple[str, ...] = ()
    requested_legal_entity_ids: tuple[str, ...] = ()
    requested_business_unit_ids: tuple[str, ...] = ()
    currency_scope: tuple[str, ...] = ()
    exception_types: tuple[APExceptionType, ...] = ()
    requested_materiality: tuple[MoneyThreshold, ...] = ()
    deliverable: APDeliverableCandidate = APDeliverableCandidate()
    include_policy_comparison: bool = True
    missing_information: tuple[str, ...] = ()
```

The model does not output tenant, user, role, data scope, purpose, approval, rule versions,
snapshot, read-only status or system limits. `require_approval=true` remains a tightening-only
transport option merged by existing Intake; it is not an LLM field.

Natural-language quarter expressions are converted to exact inclusive dates only when both year
and quarter are explicit. Arbitrary start/end dates are allowed when ordered. Relative periods
such as “last quarter” are missing information in v1 because they are not reproducible without a
separately approved reference-time rule.

## 3. Versioned contract proposal

The existing outer `TaskContract` is retained, with `constraints` generalized to a task-type-
validated union. Old serialized Supplier Quality contracts without `contract_schema_version` are
upcast as `task-contract.v1`; their fields and semantics do not change.

```python
class DateRange(ImmutableContractModel):
    start_date: date
    end_date: date


class MoneyThreshold(ImmutableContractModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    amount: Decimal = Field(ge=Decimal("0"), max_digits=20, decimal_places=4)


class APExceptionType(StrEnum):
    EXACT_DUPLICATE_INVOICE = "EXACT_DUPLICATE_INVOICE"
    PO_AMOUNT_VARIANCE = "PO_AMOUNT_VARIANCE"
    MISSING_REQUIRED_PO = "MISSING_REQUIRED_PO"
    LATE_PAYMENT = "LATE_PAYMENT"
    MATERIAL_EARLY_PAYMENT = "MATERIAL_EARLY_PAYMENT"
    OVERPAYMENT = "OVERPAYMENT"


class AccountsPayableConstraintsV1(ImmutableContractModel):
    time_range: DateRange  # required
    supplier_ids: tuple[str, ...] = ()  # resolved authorized scope
    legal_entity_ids: tuple[str, ...]  # required, 1..10
    business_unit_ids: tuple[str, ...] = ()  # resolved authorized scope
    currency_scope: tuple[str, ...] = ()  # empty = all authorized, partitioned
    exception_types: tuple[APExceptionType, ...] = ALL_AP_V1_EXCEPTIONS
    requested_materiality: tuple[MoneyThreshold, ...] = ()
    effective_materiality: tuple[MoneyThreshold, ...]  # trusted rule merge, required
    include_policy_comparison: bool = True
    tenant_id: str  # trusted, required
    data_scope: tuple[str, ...]  # trusted, required
    policy_rule_set_id: str  # trusted, required
    policy_rule_set_version: str  # trusted, required
    policy_manifest_checksum: str  # trusted, required
    snapshot_at: datetime  # trusted, required
    deadline_at: datetime  # trusted, required
    read_only: Literal[True] = True
    max_cost: Decimal | None = Field(default=None, ge=0)


class TaskContract(ImmutableContractModel):
    contract_schema_version: Literal["task-contract.v1", "task-contract.v2"]
    task_id: str
    contract_version: int = Field(ge=1)
    task_type: TaskType
    goal: str
    required_capabilities: tuple[CapabilityName, ...]
    expected_output: ExpectedOutput
    constraints: SupplierQualityConstraintsV1 | AccountsPayableConstraintsV1
    approval_requirement: ApprovalRequirement
    missing_information: tuple[str, ...] = ()
    created_at: datetime
```

AP v1 `ExpectedOutput` uses artifact type `ACCOUNTS_PAYABLE_REPORT_PDF` by default or JSON when
requested, languages `zh-CN | en-US`, citations required, and these canonical required sections:

```text
scope, data_overview, applicable_policies, exception_summary,
duplicate_invoice_findings, po_compliance_findings, payment_findings,
supplier_summary, risk_observations, recommended_actions, limitations,
evidence, execution_trace
```

## 4. Validation and defaults

| Field | Required/default | Deterministic validation |
|---|---|---|
| `goal` | required | non-blank, bounded, no authority effect |
| `task_type` | required | exact AP v1 enum; other finance intent unsupported |
| `time_range` | required | inclusive, ordered, at most 366 days |
| `supplier_ids` | trusted default | omitted resolves to authorized suppliers; max 100, unique |
| `legal_entity_ids` | conditional required | omitted only if trusted scope has exactly one; max 10 |
| `business_unit_ids` | trusted default | omitted resolves to authorized units; max 50 |
| `currency_scope` | default all authorized | unique ISO-shaped codes; no mixed-currency totals |
| `exception_types` | default all six enum values | non-empty unique subset; fuzzy/unknown fails |
| `requested_materiality` | optional | one value per currency; may only tighten policy amount |
| `effective_materiality` | required trusted result | `min(policy_amount, requested_amount)` per currency |
| `include_policy_comparison` | default `true` | if false, deterministic rules still apply; only explanatory policy sections may be shortened |
| `output_format` | default PDF | implemented formats only: PDF/JSON |
| `read_only` | required `true` | false fails validation |
| `missing_information` | default empty | non-empty means no valid executable Contract |

Cross-field rules also require requested scope to be a subset of trusted scope; each requested
materiality currency to be in `currency_scope` (or authorized currencies when scope is empty);
`snapshot_at >= time_range.end_date` for payment assessment; the rule set to cover the entire
invoice-date range; and an AP artifact type for the AP task type. A Contract with missing
information never enters Planning.

If a user tries to raise USD materiality from policy USD 10,000 to USD 50,000, the request is
rejected as `POLICY_THRESHOLD_RELAXATION_ATTEMPT`. USD 5,000 is accepted as the effective USD
5,000 threshold. Detection totals remain unchanged in either case; only WARNING/FINDING labeling
uses materiality.

## 5. Planner contract

There is one Planner. Its inputs are:

```text
validated TaskContract
+ task-type-selected DomainCapabilityManifest
+ permission-filtered ToolManifest
+ max step/retry/replan budget
+ immutable successful-step/evidence summary during replan
```

The Plan remains a DAG of the existing four StepTypes. AP manifest validation requires:

1. every requested exception type has its mapped database and analytics operation;
2. each detection analysis depends on the database Evidence required by that operation;
3. `ap.exception_summary.v1` depends on all requested detection steps;
4. the one final AP report depends on policy search and exception summary;
5. every template, operation, profile and rule version belongs to the AP manifest;
6. no raw SQL, arbitrary code, write tool or Supplier Quality profile appears;
7. total steps and recovery budgets remain bounded.

The current global `required == planned capability names` check remains useful, but exact
business wiring moves from `_supplier_quality_rules()` to the selected manifest's deterministic
`PlanRuleSet`. Supplier Quality retains its existing rules unchanged.

## 6. Repair and replan

Initial Plan repair may correct only manifest/schema/dependency errors within the same Contract.
Runtime replan may replace a failed query/detection/report step while preserving successful
Evidence and incrementing `planning_version`. Neither may change Task type, time/supplier/entity
scope, exception types, rule set, threshold, data classification or output authorization.

Missing policy-rule coverage, unsupported settlement shapes, currency mismatch, or an excessive
source population is not permission to invent a plan. The task produces a typed failure or the
explicit reason-coded exclusion behavior frozen by the analytics design.
