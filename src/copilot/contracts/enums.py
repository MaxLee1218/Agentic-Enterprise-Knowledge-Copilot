"""Canonical enums shared by all v1.1 domain contracts."""

from enum import StrEnum


class TaskType(StrEnum):
    """Versioned business task classifications admitted by governed contracts."""

    SUPPLIER_QUALITY_ANALYSIS_V1 = "supplier_quality_analysis.v1"
    ACCOUNTS_PAYABLE_ANALYSIS_V1 = "accounts_payable_analysis.v1"


class ContractSchemaVersion(StrEnum):
    """Persisted TaskContract schema versions with explicit domain bindings."""

    TASK_CONTRACT_V1 = "task-contract.v1"
    TASK_CONTRACT_V2 = "task-contract.v2"


class APExceptionType(StrEnum):
    """Deterministic Accounts Payable exception taxonomy frozen for UC2 v1."""

    EXACT_DUPLICATE_INVOICE = "EXACT_DUPLICATE_INVOICE"
    PO_AMOUNT_VARIANCE = "PO_AMOUNT_VARIANCE"
    MISSING_REQUIRED_PO = "MISSING_REQUIRED_PO"
    LATE_PAYMENT = "LATE_PAYMENT"
    MATERIAL_EARLY_PAYMENT = "MATERIAL_EARLY_PAYMENT"
    OVERPAYMENT = "OVERPAYMENT"


class APAnalyticsOperation(StrEnum):
    """Seven exact deterministic operation identifiers frozen for AP v1."""

    EXACT_DUPLICATE_INVOICE_DETECTION = "ap.exact_duplicate_invoice_detection.v1"
    INVOICE_PO_VARIANCE_DETECTION = "ap.invoice_po_variance_detection.v1"
    MISSING_PO_DETECTION = "ap.missing_po_detection.v1"
    PAYMENT_TERM_COMPLIANCE_DETECTION = "ap.payment_term_compliance_detection.v1"
    OVERPAYMENT_DETECTION = "ap.overpayment_detection.v1"
    EXCEPTION_SUMMARY = "ap.exception_summary.v1"
    SUPPLIER_EXCEPTION_RATE = "ap.supplier_exception_rate.v1"


class APDatabaseTemplate(StrEnum):
    """Five governed read-model identifiers frozen for AP v1."""

    INVOICE_POPULATION = "ap_invoice_population_v1"
    DUPLICATE_CANDIDATES = "ap_duplicate_invoice_candidates_v1"
    INVOICE_PO_VARIANCE = "ap_invoice_po_variance_v1"
    PAYMENT_TERMS = "ap_payment_terms_v1"
    PAYMENT_AMOUNT = "ap_payment_amount_v1"


class TaskStatus(StrEnum):
    """Authoritative lifecycle states from the frozen task state machine."""

    CREATED = "CREATED"
    UNDERSTANDING = "UNDERSTANDING"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RETRYING = "RETRYING"
    REPLANNING = "REPLANNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepType(StrEnum):
    """Executable step categories supported in v1.0 plans."""

    KNOWLEDGE_SEARCH = "KNOWLEDGE_SEARCH"
    DATABASE_QUERY = "DATABASE_QUERY"
    ANALYSIS = "ANALYSIS"
    REPORT_GENERATION = "REPORT_GENERATION"


class StepResultStatus(StrEnum):
    """Normalized final outcomes for an execution step."""

    SUCCESS = "SUCCESS"
    BUSINESS_FAILURE = "BUSINESS_FAILURE"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    TIMEOUT = "TIMEOUT"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    CANCELLED = "CANCELLED"


class ToolResultStatus(StrEnum):
    """Normalized outcomes for one tool invocation attempt."""

    SUCCESS = "SUCCESS"
    BUSINESS_FAILURE = "BUSINESS_FAILURE"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    TIMEOUT = "TIMEOUT"
    PERMISSION_DENIED = "PERMISSION_DENIED"


class EvidenceType(StrEnum):
    """Evidence source types permitted by the frozen v1.0 scenario."""

    DOCUMENT = "DOCUMENT"
    DATABASE = "DATABASE"
    CALCULATION = "CALCULATION"


class ErrorType(StrEnum):
    """Stable error categories shared across nodes and tools."""

    BUSINESS = "BUSINESS"
    TECHNICAL = "TECHNICAL"
    TIMEOUT = "TIMEOUT"
    PERMISSION = "PERMISSION"
    VALIDATION = "VALIDATION"
    CANCELLATION = "CANCELLATION"


class RiskLevel(StrEnum):
    """Governed risk levels for registered capabilities."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ApprovalStatus(StrEnum):
    """Immutable approval decision states."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class ApprovalResolutionAction(StrEnum):
    """One-time human action used to resolve a pending approval."""

    APPROVE = "APPROVE"
    EDIT = "EDIT"
    REJECT = "REJECT"


class ArtifactType(StrEnum):
    """Versioned internal report Artifact types admitted by contracts."""

    QUALITY_ANALYSIS_REPORT_PDF = "QUALITY_ANALYSIS_REPORT_PDF"
    QUALITY_ANALYSIS_REPORT_JSON = "QUALITY_ANALYSIS_REPORT_JSON"
    ACCOUNTS_PAYABLE_REPORT_PDF = "ACCOUNTS_PAYABLE_REPORT_PDF"
    ACCOUNTS_PAYABLE_REPORT_JSON = "ACCOUNTS_PAYABLE_REPORT_JSON"


class CapabilityName(StrEnum):
    """Tool capabilities approved for the v1.0 scenario."""

    KNOWLEDGE_SEARCH = "knowledge_search"
    DATABASE_QUERY = "database_query"
    ANALYSIS_ENGINE = "analysis_engine"
    REPORT_GENERATOR = "report_generator"


class ReportLanguage(StrEnum):
    """Report languages supported by the v1.0 renderer."""

    ZH_CN = "zh-CN"
    EN_US = "en-US"
