"""Canonical enums shared by all v1.0 domain contracts."""

from enum import StrEnum


class TaskType(StrEnum):
    """Business task types supported by the frozen v1.0 baseline."""

    SUPPLIER_QUALITY_ANALYSIS_V1 = "supplier_quality_analysis.v1"


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


class ArtifactType(StrEnum):
    """Deliverable artifact types supported in v1.0."""

    QUALITY_ANALYSIS_REPORT_PDF = "QUALITY_ANALYSIS_REPORT_PDF"
    QUALITY_ANALYSIS_REPORT_JSON = "QUALITY_ANALYSIS_REPORT_JSON"


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
