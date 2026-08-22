"""Task request, contract, lifecycle snapshot, and final result models."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from copilot.contracts.base import ImmutableContractModel, JsonObject
from copilot.contracts.enums import (
    APExceptionType,
    ArtifactType,
    CapabilityName,
    ContractSchemaVersion,
    ReportLanguage,
    TaskStatus,
    TaskType,
)
from copilot.contracts.validators import utc_now, validate_identifier, validate_utc_datetime


class TaskRequest(ImmutableContractModel):
    """Immutable authenticated user request and audit-chain starting point."""

    id: str = Field(description="Globally unique request identifier", min_length=1)
    user_id: str = Field(description="Authenticated request creator identifier", min_length=1)
    raw_input: str = Field(description="Original user request preserved verbatim", min_length=1)
    created_at: datetime = Field(default_factory=utc_now, description="UTC request receipt time")
    metadata: JsonObject = Field(
        default_factory=lambda: JsonObject({}),
        description="Non-core, non-secret request extension metadata",
    )

    _validate_ids = field_validator("id", "user_id")(validate_identifier)
    _validate_created_at = field_validator("created_at")(validate_utc_datetime)

    @field_validator("raw_input")
    @classmethod
    def validate_raw_input(cls, value: str) -> str:
        """Preserve the original request while rejecting whitespace-only input."""
        if not value.strip():
            raise ValueError("raw_input must not be blank")
        return value


class ExpectedOutput(ImmutableContractModel):
    """Explicit report and citation requirements for a task contract."""

    artifact_type: ArtifactType = Field(description="Required deliverable artifact type")
    required_sections: tuple[str, ...] = Field(
        description="Report sections that must be present", min_length=1
    )
    language: ReportLanguage = Field(description="Required report language")
    citations_required: bool = Field(description="Whether material claims require citations")

    _validate_sections = field_validator("required_sections")(
        lambda values: tuple(validate_identifier(value) for value in values)
    )


class TaskConstraints(ImmutableContractModel):
    """Authorized, time-bounded business scope for supplier quality analysis."""

    year: int = Field(description="Calendar year covered by the analysis", ge=2000, le=9999)
    quarter: int = Field(description="Calendar quarter covered by the analysis", ge=1, le=4)
    start_date: date = Field(description="Inclusive first date of the analysis period")
    end_date: date = Field(description="Inclusive last date of the analysis period")
    supplier_ids: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Authorized suppliers; empty means all in the resolved scope",
    )
    tenant_id: str = Field(description="Authenticated tenant identifier", min_length=1)
    data_scope: tuple[str, ...] = Field(description="Explicit authorized data scope", min_length=1)
    metrics: tuple[str, ...] = Field(description="Deterministic metrics requested", min_length=1)
    deadline_at: datetime = Field(description="UTC deadline for the entire task")
    max_cost: Decimal | None = Field(
        default=None,
        description="Optional maximum cost in configured accounting units",
        ge=0,
    )

    _validate_tenant = field_validator("tenant_id")(validate_identifier)
    _validate_deadline = field_validator("deadline_at")(validate_utc_datetime)
    _validate_string_collections = field_validator("supplier_ids", "data_scope", "metrics")(
        lambda values: tuple(validate_identifier(value) for value in values)
    )

    @model_validator(mode="after")
    def validate_date_range(self) -> "TaskConstraints":
        """Require an ordered date range belonging to the declared year and quarter."""
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        start_quarter = (self.start_date.month - 1) // 3 + 1
        end_quarter = (self.end_date.month - 1) // 3 + 1
        if (
            self.start_date.year != self.year
            or self.end_date.year != self.year
            or start_quarter != self.quarter
            or end_quarter != self.quarter
        ):
            raise ValueError("date range must remain inside the declared year and quarter")
        if len(set(self.supplier_ids)) != len(self.supplier_ids):
            raise ValueError("supplier_ids must be unique")
        return self


SupplierQualityConstraintsV1 = TaskConstraints


class DateRange(ImmutableContractModel):
    """Inclusive, bounded calendar range for Accounts Payable analysis."""

    start_date: date = Field(description="Inclusive first invoice date")
    end_date: date = Field(description="Inclusive last invoice date")

    @model_validator(mode="after")
    def validate_range(self) -> "DateRange":
        """Require an ordered range of no more than 366 inclusive days."""
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        if (self.end_date - self.start_date).days + 1 > 366:
            raise ValueError("Accounts Payable time range must not exceed 366 inclusive days")
        return self


class MoneyThreshold(ImmutableContractModel):
    """Currency-qualified Decimal threshold used by controlled AP policy rules."""

    currency: str = Field(pattern=r"^[A-Z]{3}$")
    amount: Decimal = Field(ge=Decimal("0"), max_digits=20, decimal_places=4)


class AccountsPayableConstraintsV1(ImmutableContractModel):
    """Trusted, read-only scope for Accounts Payable investigation v1."""

    time_range: DateRange
    supplier_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    legal_entity_ids: tuple[str, ...] = Field(min_length=1, max_length=10)
    business_unit_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    currency_scope: tuple[str, ...] = Field(default_factory=tuple)
    exception_types: tuple[APExceptionType, ...] = Field(
        default_factory=lambda: tuple(APExceptionType), min_length=1
    )
    requested_materiality: tuple[MoneyThreshold, ...] = Field(default_factory=tuple)
    effective_materiality: tuple[MoneyThreshold, ...] = Field(min_length=1)
    include_policy_comparison: bool = True
    tenant_id: str = Field(min_length=1)
    data_scope: tuple[str, ...] = Field(min_length=1)
    policy_rule_set_id: str = Field(min_length=1)
    policy_rule_set_version: str = Field(min_length=1)
    policy_manifest_checksum: str = Field(min_length=1)
    snapshot_at: datetime
    deadline_at: datetime
    read_only: bool = True
    max_cost: Decimal | None = Field(default=None, ge=0)

    _validate_ap_identifiers = field_validator(
        "supplier_ids",
        "legal_entity_ids",
        "business_unit_ids",
        "data_scope",
    )(lambda values: tuple(validate_identifier(value) for value in values))
    _validate_ap_tenant = field_validator("tenant_id")(validate_identifier)
    _validate_ap_timestamps = field_validator("snapshot_at", "deadline_at")(validate_utc_datetime)

    @field_validator("currency_scope")
    @classmethod
    def validate_currencies(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Require unique uppercase ISO-shaped currency codes."""
        if any(
            len(value) != 3 or not value.isascii() or not value.isalpha() or not value.isupper()
            for value in values
        ):
            raise ValueError("currency_scope must contain uppercase three-letter codes")
        if len(set(values)) != len(values):
            raise ValueError("currency_scope must be unique")
        return values

    @model_validator(mode="after")
    def validate_ap_scope(self) -> "AccountsPayableConstraintsV1":
        """Reject ambiguous, duplicate, stale, or policy-relaxing AP scope."""
        collections = (
            ("supplier_ids", self.supplier_ids),
            ("legal_entity_ids", self.legal_entity_ids),
            ("business_unit_ids", self.business_unit_ids),
            ("exception_types", self.exception_types),
        )
        for name, values in collections:
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must be unique")
        if not self.read_only:
            raise ValueError("Accounts Payable analysis must be read-only")
        if self.policy_rule_set_version != "ap_rules.2026.1":
            raise ValueError("policy_rule_set_version must be ap_rules.2026.1")
        if self.snapshot_at.date() < self.time_range.end_date:
            raise ValueError("snapshot_at must cover the complete invoice-date range")
        if self.deadline_at <= self.snapshot_at:
            raise ValueError("deadline_at must be after snapshot_at")

        requested = _thresholds_by_currency(
            self.requested_materiality, field_name="requested_materiality"
        )
        effective = _thresholds_by_currency(
            self.effective_materiality, field_name="effective_materiality"
        )
        allowed_currencies = set(self.currency_scope)
        if allowed_currencies and not set(requested).issubset(allowed_currencies):
            raise ValueError("requested materiality currency is outside currency_scope")
        if allowed_currencies and set(effective) != allowed_currencies:
            raise ValueError("effective_materiality must cover currency_scope exactly")
        for currency, threshold in requested.items():
            if currency not in effective or effective[currency] != threshold:
                raise ValueError(
                    "requested materiality must equal the trusted effective tightening"
                )
        return self


def _thresholds_by_currency(
    thresholds: tuple[MoneyThreshold, ...], *, field_name: str
) -> dict[str, Decimal]:
    values = {item.currency: item.amount for item in thresholds}
    if len(values) != len(thresholds):
        raise ValueError(f"{field_name} must contain at most one threshold per currency")
    return values


class ApprovalRequirement(ImmutableContractModel):
    """Policy-derived human approval requirement bound to a controlled scope."""

    required: bool = Field(description="Whether human approval is required")
    policy_id: str | None = Field(default=None, description="Policy requiring approval")
    approver_role: str | None = Field(default=None, description="Role authorized to approve")
    controlled_scope: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Actions and data scope covered by the approval",
    )

    @model_validator(mode="after")
    def validate_required_fields(self) -> "ApprovalRequirement":
        """Require policy and role bindings whenever approval is required."""
        if self.required and (not self.policy_id or not self.approver_role):
            raise ValueError("required approval must include policy_id and approver_role")
        return self


class TaskContract(ImmutableContractModel):
    """Versioned, verifiable, and authorizable interpretation of a request."""

    contract_schema_version: ContractSchemaVersion
    task_id: str = Field(description="Task governed by this contract", min_length=1)
    contract_version: int = Field(description="Monotonically increasing contract version", ge=1)
    task_type: TaskType = Field(description="Supported business task classification")
    goal: str = Field(
        description="Bounded business objective with no authorization effect",
        min_length=1,
        max_length=2_000,
    )
    required_capabilities: tuple[CapabilityName, ...] = Field(
        description="Registered capabilities required to fulfill the contract", min_length=1
    )
    expected_output: ExpectedOutput = Field(description="Required deliverable contract")
    constraints: TaskConstraints | AccountsPayableConstraintsV1 = Field(
        description="Task-type-bound authorized business and execution scope"
    )
    approval_requirement: ApprovalRequirement = Field(
        description="Policy-derived approval requirement"
    )
    created_at: datetime = Field(default_factory=utc_now, description="UTC contract creation time")
    missing_information: tuple[str, ...] = Field(default_factory=tuple)

    _validate_task_id = field_validator("task_id")(validate_identifier)
    _validate_created_at = field_validator("created_at")(validate_utc_datetime)

    @field_validator("goal")
    @classmethod
    def validate_goal(cls, value: str) -> str:
        """Reject a whitespace-only goal without normalizing the recorded objective."""
        if not value.strip():
            raise ValueError("goal must not be blank")
        return value

    @model_validator(mode="after")
    def validate_capabilities(self) -> "TaskContract":
        """Reject duplicate capabilities and cross-domain contract substitution."""
        if len(set(self.required_capabilities)) != len(self.required_capabilities):
            raise ValueError("required_capabilities must be unique")
        quality_artifacts = {
            ArtifactType.QUALITY_ANALYSIS_REPORT_PDF,
            ArtifactType.QUALITY_ANALYSIS_REPORT_JSON,
        }
        ap_artifacts = {
            ArtifactType.ACCOUNTS_PAYABLE_REPORT_PDF,
            ArtifactType.ACCOUNTS_PAYABLE_REPORT_JSON,
        }
        if self.task_type is TaskType.SUPPLIER_QUALITY_ANALYSIS_V1:
            if self.contract_schema_version is not ContractSchemaVersion.TASK_CONTRACT_V1:
                raise ValueError("Supplier Quality requires task-contract.v1")
            if not isinstance(self.constraints, TaskConstraints):
                raise ValueError("Supplier Quality requires Supplier Quality constraints")
            if self.expected_output.artifact_type not in quality_artifacts:
                raise ValueError("Supplier Quality requires a quality report artifact")
        elif self.task_type is TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1:
            if self.contract_schema_version is not ContractSchemaVersion.TASK_CONTRACT_V2:
                raise ValueError("Accounts Payable requires task-contract.v2")
            if not isinstance(self.constraints, AccountsPayableConstraintsV1):
                raise ValueError("Accounts Payable requires Accounts Payable constraints")
            if self.expected_output.artifact_type not in ap_artifacts:
                raise ValueError("Accounts Payable requires an Accounts Payable report artifact")
        return self


class TaskState(ImmutableContractModel):
    """Authoritative versioned lifecycle snapshot used for recovery and concurrency."""

    task_id: str = Field(description="Task represented by this state snapshot", min_length=1)
    state: TaskStatus = Field(description="Current state-machine lifecycle state")
    version: int = Field(description="Monotonic compare-and-swap state version", ge=1)
    updated_at: datetime = Field(description="UTC time of the latest legal transition")
    last_event_id: str = Field(description="Audit event that produced this snapshot", min_length=1)

    _validate_ids = field_validator("task_id", "last_event_id")(validate_identifier)
    _validate_updated_at = field_validator("updated_at")(validate_utc_datetime)


class TaskResult(ImmutableContractModel):
    """Immutable external task result produced exactly once in a terminal state."""

    task_id: str = Field(description="Terminal task identifier", min_length=1)
    final_status: TaskStatus = Field(description="Terminal lifecycle status")
    summary: str = Field(description="Evidence-safe outcome or termination summary", min_length=1)
    artifacts: tuple[str, ...] = Field(
        default_factory=tuple, description="Artifact identifiers in the final result"
    )
    evidence: tuple[str, ...] = Field(
        default_factory=tuple, description="Evidence identifiers supporting the result"
    )

    _validate_task_id = field_validator("task_id")(validate_identifier)

    @model_validator(mode="after")
    def validate_terminal_status(self) -> "TaskResult":
        """Allow final results only for terminal lifecycle states."""
        terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        if self.final_status not in terminal:
            raise ValueError("final_status must be a terminal task state")
        return self
