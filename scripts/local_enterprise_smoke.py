"""Exercise the browser-facing Local Enterprise E2E deployment over real HTTP."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_FILE = PROJECT_ROOT / "docker-compose.local-enterprise.yml"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env.local-enterprise"
REQUIRED_TOOLS = {
    "knowledge_search",
    "database_query",
    "analysis_engine",
    "report_generator",
}
REQUIRED_EVIDENCE_TYPES = {"DOCUMENT", "DATABASE", "CALCULATION"}
FORMAL_RAG_IMAGE = "enterprise-rag-engine:local"
FORMAL_DOCUMENT_SOURCES = {
    "Incoming_Inspection_Procedure.pdf",
    "Quality_Escalation_Procedure.pdf",
    "Supplier_Nonconformance_Policy.pdf",
    "Supplier_Quality_KPI_Definitions.pdf",
    "Supplier_Quality_Manual.pdf",
}
FIXTURE_SOURCE_MARKERS = ("fixture", "supplier-quality-policy-v1", "stage17")
AP_POLICY_DOCUMENTS = {
    "accounts-payable-policy",
    "procurement-and-purchase-order-policy",
    "invoice-approval-and-delegation-policy",
    "payment-terms-policy",
}
AP_TOOL_COUNTS = {
    "knowledge_search": 1,
    "database_query": 5,
    "analysis_engine": 7,
    "report_generator": 1,
}
AP_RESTRICTED_FIELDS = {
    "bank_account",
    "iban",
    "swift",
    "tax_id",
    "payment_reference",
    "internal_account_number",
}
AP_EXPECTED_EXCEPTION_TYPES = {
    "EXACT_DUPLICATE_INVOICE": 1,
    "PO_AMOUNT_VARIANCE": 1,
    "MISSING_REQUIRED_PO": 1,
    "LATE_PAYMENT": 1,
    "MATERIAL_EARLY_PAYMENT": 2,
    "OVERPAYMENT": 1,
}


class SmokeFailure(RuntimeError):
    """One safe, actionable Local Enterprise verification failure."""


@dataclass(frozen=True, slots=True)
class CompletedTask:
    """Safe identifiers and verified Artifact bytes from one completed task."""

    task_id: str
    trace_id: str
    artifact_id: str
    checksum: str
    output_format: str
    content: bytes


def _request(
    url: str,
    *,
    method: str = "GET",
    payload: Mapping[str, object] | None = None,
    timeout_seconds: float = 330,
) -> tuple[int, Mapping[str, Any], bytes]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            content = response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        content = error.read()
        status = error.code
    parsed: Mapping[str, Any] = {}
    if content:
        try:
            candidate = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            candidate = None
        if isinstance(candidate, dict):
            parsed = candidate
    return status, parsed, content


def _json_ok(
    url: str,
    *,
    method: str = "GET",
    payload: Mapping[str, object] | None = None,
) -> Mapping[str, Any]:
    status, result, _body = _request(url, method=method, payload=payload)
    if not 200 <= status < 300:
        code = result.get("error_code", f"HTTP_{status}")
        message = result.get("message", "request failed")
        raise SmokeFailure(f"{code}: {message}")
    return result


def _wait_for(
    description: str,
    predicate: Callable[[], bool],
    *,
    timeout_seconds: float = 180,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (OSError, SmokeFailure):
            pass
        time.sleep(2)
    raise SmokeFailure(f"Timed out waiting for {description}")


def _download_and_verify(url: str, expected_checksum: str) -> bytes:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content = response.read()
    except urllib.error.HTTPError as error:
        raise SmokeFailure(f"Artifact download failed with HTTP {error.code}") from error
    actual = hashlib.sha256(content).hexdigest()
    normalized = expected_checksum.removeprefix("sha256:")
    if not content or actual != normalized:
        raise SmokeFailure("Downloaded Artifact checksum does not match persisted metadata")
    return bytes(content)


def _require_mapping(value: object, description: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SmokeFailure(f"{description} is not an object")
    return value


def _require_list(value: object, description: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise SmokeFailure(f"{description} is not a list")
    if any(not isinstance(item, dict) for item in value):
        raise SmokeFailure(f"{description} contains a non-object item")
    return [item for item in value if isinstance(item, dict)]


def _assert_no_restricted_keys(value: object, *, path: str = "report") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().casefold()
            if normalized in AP_RESTRICTED_FIELDS:
                raise SmokeFailure(f"Restricted finance field entered the Artifact at {path}")
            _assert_no_restricted_keys(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_restricted_keys(child, path=f"{path}[{index}]")


def _assert_no_secret_material(contents: Sequence[bytes], secrets: Sequence[str]) -> None:
    for secret in secrets:
        encoded = secret.encode("utf-8")
        if len(encoded) < 8:
            continue
        if any(encoded in content for content in contents):
            raise SmokeFailure("A configured secret value entered an HTTP response or Artifact")


def _task_resources(
    base_url: str,
    task_id: str,
    *,
    expected_steps: int,
    expected_tool_counts: Mapping[str, int],
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    task_url = f"{base_url}/api/v1/tasks/{task_id}"
    detail = _json_ok(task_url)
    steps = _require_list(_json_ok(f"{task_url}/steps").get("steps"), "Task steps")
    evidence = _require_list(
        _json_ok(f"{task_url}/evidence").get("evidence"),
        "Task Evidence",
    )
    if detail.get("status") != "COMPLETED":
        raise SmokeFailure("Persisted task detail is not COMPLETED")
    if len(steps) != expected_steps:
        raise SmokeFailure(f"Task has {len(steps)} steps; expected {expected_steps}")
    observed_counts = {
        tool: sum(item.get("tool_name") == tool for item in steps)
        for tool in {str(item.get("tool_name")) for item in steps}
    }
    if observed_counts != dict(expected_tool_counts):
        raise SmokeFailure(f"Task tool counts differ from the frozen plan: {observed_counts}")
    if any(item.get("status") != "SUCCESS" for item in steps):
        raise SmokeFailure("At least one governed task step is not SUCCESS")
    if any(item.get("attempt_count") != 1 or item.get("retry_count") != 0 for item in steps):
        raise SmokeFailure("The deterministic E2E path unexpectedly retried or skipped a step")
    evidence_types = {str(item.get("type")) for item in evidence}
    if not REQUIRED_EVIDENCE_TYPES.issubset(evidence_types):
        raise SmokeFailure(f"Required Evidence types are missing: {sorted(evidence_types)}")
    return detail, steps, evidence


def _task_artifact(
    base_url: str,
    task_id: str,
    output_format: str,
) -> CompletedTask:
    task_url = f"{base_url}/api/v1/tasks/{task_id}"
    artifacts = _require_list(
        _json_ok(f"{task_url}/artifacts").get("artifacts"),
        "Task Artifacts",
    )
    matching = [
        item for item in artifacts if str(item.get("format", "")).casefold() == output_format
    ]
    if len(matching) != 1:
        raise SmokeFailure(f"Expected exactly one {output_format.upper()} Artifact")
    artifact = matching[0]
    artifact_id = artifact.get("artifact_id")
    checksum = artifact.get("checksum")
    if not isinstance(artifact_id, str) or not isinstance(checksum, str):
        raise SmokeFailure("Artifact metadata lacks its ID or checksum")
    content = _download_and_verify(f"{task_url}/artifacts/{artifact_id}", checksum)
    detail = _json_ok(task_url)
    trace_id = detail.get("trace_id")
    if not isinstance(trace_id, str) or not trace_id:
        raise SmokeFailure("Persisted task detail does not include trace_id")
    return CompletedTask(
        task_id=task_id,
        trace_id=trace_id,
        artifact_id=artifact_id,
        checksum=checksum,
        output_format=output_format,
        content=content,
    )


def _verify_json_traceability(content: bytes, *, require_formal_rag: bool) -> None:
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise SmokeFailure("JSON Artifact is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise SmokeFailure("JSON Artifact root is not an object")
    references = payload.get("evidence_references")
    if not isinstance(references, list):
        raise SmokeFailure("JSON Artifact lacks its Evidence references")

    by_type = {
        item.get("source_type"): item
        for item in references
        if isinstance(item, dict) and isinstance(item.get("source_type"), str)
    }
    document = by_type.get("DOCUMENT")
    document_source = document.get("source") if isinstance(document, dict) else None
    if not isinstance(document_source, dict) or not all(
        document_source.get(field) for field in ("document_id", "chunk_id", "rag_trace_id")
    ):
        raise SmokeFailure("DOCUMENT Evidence lacks document, chunk, or RAG trace metadata")
    if require_formal_rag:
        _require_formal_source(str(document_source["document_id"]))

    database = by_type.get("DATABASE")
    database_source = database.get("source") if isinstance(database, dict) else None
    if (
        not isinstance(database, dict)
        or not isinstance(database_source, dict)
        or database_source.get("database_name") != "supplier_quality"
        or database_source.get("read_only") is not True
        or not database_source.get("row_count")
        or not database_source.get("query_fingerprint")
        or not {"suppliers", "incoming_inspections"}.issubset(
            set(database_source.get("table_names", []))
        )
        or not database.get("checksum")
    ):
        raise SmokeFailure("DATABASE Evidence lacks real read-only query lineage")

    calculation = by_type.get("CALCULATION")
    analysis = payload.get("analysis_results")
    if (
        not isinstance(calculation, dict)
        or not calculation.get("formulas")
        or not calculation.get("input_evidence_ids")
        or not isinstance(analysis, dict)
        or not analysis.get("dataset_checksum")
        or not analysis.get("input_row_count")
        or not analysis.get("calculation_version")
    ):
        raise SmokeFailure("CALCULATION Evidence lacks formula, dataset, or input lineage")


def _run_supplier_task(
    base_url: str,
    output_format: str,
    *,
    require_formal_rag: bool,
) -> CompletedTask:
    task_text = (
        "Analyze supplier quality for Q2 2026, compare it with the previous period, "
        "check the approved supplier quality policy, and generate a "
        f"{output_format.upper()} management report."
    )
    created = _json_ok(
        f"{base_url}/api/v1/tasks",
        method="POST",
        payload={"task": task_text, "output_format": output_format},
    )
    task_id = created.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise SmokeFailure("Task submission did not return a task_id")
    if created.get("status") != "COMPLETED":
        raise SmokeFailure(f"{output_format.upper()} task ended in {created.get('status')}")

    task_url = f"{base_url}/api/v1/tasks/{task_id}"
    detail = _json_ok(task_url)
    if detail.get("status") != "COMPLETED":
        raise SmokeFailure("Persisted task detail is not COMPLETED")
    trace_id = detail.get("trace_id")
    if not isinstance(trace_id, str) or not trace_id:
        raise SmokeFailure("Persisted task detail does not include trace_id")

    steps_payload = _json_ok(f"{task_url}/steps")
    raw_steps = steps_payload.get("steps")
    if not isinstance(raw_steps, list):
        raise SmokeFailure("Task steps response is malformed")
    steps = [item for item in raw_steps if isinstance(item, dict)]
    tools = {str(item.get("tool_name")) for item in steps}
    if tools != REQUIRED_TOOLS:
        raise SmokeFailure(f"Task tools differ from the frozen four-tool plan: {sorted(tools)}")
    if any(item.get("status") != "SUCCESS" for item in steps):
        raise SmokeFailure("At least one governed task step is not SUCCESS")

    evidence_payload = _json_ok(f"{task_url}/evidence")
    raw_evidence = evidence_payload.get("evidence")
    if not isinstance(raw_evidence, list):
        raise SmokeFailure("Task Evidence response is malformed")
    evidence = [item for item in raw_evidence if isinstance(item, dict)]
    evidence_types = {str(item.get("type")) for item in evidence}
    if not REQUIRED_EVIDENCE_TYPES.issubset(evidence_types):
        raise SmokeFailure(f"Required Evidence types are missing: {sorted(evidence_types)}")
    documents = [item for item in evidence if item.get("type") == "DOCUMENT"]
    databases = [item for item in evidence if item.get("type") == "DATABASE"]
    calculations = [item for item in evidence if item.get("type") == "CALCULATION"]
    if not any(item.get("document_source") for item in documents):
        raise SmokeFailure("DOCUMENT Evidence lacks a source reference")
    if require_formal_rag:
        for item in documents:
            source = item.get("document_source")
            if isinstance(source, str) and source:
                _require_formal_source(source)
    if not any(item.get("query_id") for item in databases):
        raise SmokeFailure("DATABASE Evidence lacks a query fingerprint")
    if not any(item.get("formula") and item.get("input_evidence_ids") for item in calculations):
        raise SmokeFailure("CALCULATION Evidence lacks formula or input lineage")

    artifact_payload = _json_ok(f"{task_url}/artifacts")
    raw_artifacts = artifact_payload.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise SmokeFailure("Task Artifact response is malformed")
    artifacts = [item for item in raw_artifacts if isinstance(item, dict)]
    matching = [item for item in artifacts if str(item.get("format", "")).lower() == output_format]
    if not matching:
        raise SmokeFailure(f"No {output_format.upper()} Artifact was published")
    artifact = matching[0]
    artifact_id = artifact.get("artifact_id")
    checksum = artifact.get("checksum")
    if not isinstance(artifact_id, str) or not isinstance(checksum, str):
        raise SmokeFailure("Artifact metadata lacks its ID or checksum")
    content = _download_and_verify(f"{task_url}/artifacts/{artifact_id}", checksum)
    if output_format == "json":
        _verify_json_traceability(content, require_formal_rag=require_formal_rag)
    print(
        f"PASS Supplier Quality {output_format.upper()} task: task_id={task_id} "
        f"steps={len(steps)} evidence={len(evidence)} artifact_id={artifact_id}"
    )
    return CompletedTask(
        task_id=task_id,
        trace_id=trace_id,
        artifact_id=artifact_id,
        checksum=checksum,
        output_format=output_format,
        content=content,
    )


def _ap_task_text(*, clean: bool, output_format: str) -> str:
    if clean:
        return (
            "Analyze all Accounts Payable exceptions from 2026-06-01 to 2026-06-01 "
            f"for LE-US-01 and generate a {output_format.upper()} report."
        )
    return (
        "Analyze all Accounts Payable exceptions from 2026-04-01 to 2026-06-30 "
        f"for LE-CN-01 and LE-US-01 and generate a {output_format.upper()} report."
    )


def _verify_ap_resources(
    base_url: str,
    task_id: str,
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    detail, steps, evidence = _task_resources(
        base_url,
        task_id,
        expected_steps=14,
        expected_tool_counts=AP_TOOL_COUNTS,
    )
    if detail.get("task_type") != "accounts_payable_analysis.v1":
        raise SmokeFailure("AP task was persisted under the wrong domain type")
    by_type = {
        evidence_type: [item for item in evidence if item.get("type") == evidence_type]
        for evidence_type in REQUIRED_EVIDENCE_TYPES
    }
    if len(by_type["DATABASE"]) != 5 or len(by_type["CALCULATION"]) != 7:
        raise SmokeFailure(
            "AP task did not persist the frozen five-query/seven-calculation lineage"
        )
    document_sources = {
        str(item.get("document_source"))
        for item in by_type["DOCUMENT"]
        if item.get("document_source")
    }
    if not document_sources or not document_sources.issubset(AP_POLICY_DOCUMENTS):
        raise SmokeFailure("AP DOCUMENT Evidence is outside the controlled policy corpus")
    if any(not item.get("query_id") for item in by_type["DATABASE"]):
        raise SmokeFailure("AP DATABASE Evidence lacks a query fingerprint")
    if any(
        item.get("source") != "accounts_payable_analytics.v1"
        or not item.get("input_evidence_ids")
        or not item.get("lineage")
        for item in by_type["CALCULATION"]
    ):
        raise SmokeFailure("AP CALCULATION Evidence lacks profile or input lineage")
    return detail, steps, evidence


def _verify_ap_json(content: bytes, *, clean: bool) -> Mapping[str, Any]:
    try:
        raw = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise SmokeFailure("AP JSON Artifact is not valid UTF-8 JSON") from error
    payload = _require_mapping(raw, "AP JSON Artifact")
    _assert_no_restricted_keys(payload)
    metadata = _require_mapping(payload.get("execution_metadata"), "AP execution metadata")
    expected_versions = {
        "schema_version": "accounts_payable_report_model.v1",
        "template_version": "accounts_payable_report.v1",
        "generator_version": "report_generator.v2",
        "rule_set_version": "ap_rules.2026.1",
        "detail_access": "DETAIL",
    }
    if any(metadata.get(name) != value for name, value in expected_versions.items()):
        raise SmokeFailure("AP report profile or rule versions differ from the frozen contracts")
    if metadata.get("policy_manifest_checksum") != (
        "sha256:3095ebb099a2db12dffbc699cf1f65bb7d8e324d025eb701af4bf825d6adab33"
    ):
        raise SmokeFailure("AP report does not bind the frozen policy manifest checksum")

    summary = _require_mapping(payload.get("exception_summary"), "AP exception summary")
    metrics = _require_mapping(summary.get("metrics"), "AP exception metrics")
    expected_counts = (
        {"invoice_count": 1, "exception_invoice_count": 0}
        if clean
        else {"invoice_count": 23, "exception_invoice_count": 7}
    )
    if any(metrics.get(name) != value for name, value in expected_counts.items()):
        raise SmokeFailure(f"AP summary counts differ from the reviewed oracle: {metrics}")
    if clean:
        if summary.get("finding_count") != 0 or summary.get("warning_count") != 0:
            raise SmokeFailure("The clean AP control unexpectedly contains an exception")
    else:
        if (
            metrics.get("exception_rate") != "0.30434783"
            or summary.get("finding_count") != 5
            or summary.get("warning_count") != 2
        ):
            raise SmokeFailure("AP mixed-exception summary differs from the reviewed oracle")
        findings = [
            *_require_list(
                payload.get("duplicate_invoice_findings"),
                "AP duplicate findings",
            ),
            *_require_list(payload.get("po_compliance_findings"), "AP PO findings"),
            *_require_list(payload.get("payment_findings"), "AP payment findings"),
        ]
        observed = {
            exception_type: sum(item.get("exception_type") == exception_type for item in findings)
            for exception_type in AP_EXPECTED_EXCEPTION_TYPES
        }
        if observed != AP_EXPECTED_EXCEPTION_TYPES:
            raise SmokeFailure(f"AP typed findings differ from the reviewed oracle: {observed}")

    evidence = _require_mapping(payload.get("evidence"), "AP report Evidence")
    references = _require_list(evidence.get("references"), "AP report Evidence references")
    if {str(item.get("source_type")) for item in references} != REQUIRED_EVIDENCE_TYPES:
        raise SmokeFailure("AP report does not retain all required Evidence reference types")
    if not _require_list(evidence.get("claims"), "AP report claims"):
        raise SmokeFailure("AP report has no evidence-backed claims")
    return payload


def _verify_ap_pdf(content: bytes) -> None:
    if not content.startswith(b"%PDF-"):
        raise SmokeFailure("AP PDF Artifact lacks the PDF signature")
    lowered = content.lower()
    if any(f'"{field}"'.encode() in lowered for field in AP_RESTRICTED_FIELDS):
        raise SmokeFailure("Restricted finance fields entered the AP PDF Artifact")


def _run_ap_task(
    base_url: str,
    output_format: str,
    *,
    clean: bool,
) -> CompletedTask:
    status, created, body = _request(
        f"{base_url}/api/v1/tasks",
        method="POST",
        payload={
            "task": _ap_task_text(clean=clean, output_format=output_format),
            "task_type": "accounts_payable_analysis.v1",
            "output_format": output_format,
            "max_steps": 14,
        },
    )
    if status != 201:
        code = created.get("error_code", f"HTTP_{status}")
        raise SmokeFailure(f"AP submission failed: {code}")
    task_id = created.get("task_id")
    if not isinstance(task_id, str) or created.get("status") != "COMPLETED":
        raise SmokeFailure("AP task did not complete synchronously")
    _verify_ap_resources(base_url, task_id)
    completed = _task_artifact(base_url, task_id, output_format)
    if output_format == "json":
        _verify_ap_json(completed.content, clean=clean)
    else:
        _verify_ap_pdf(completed.content)
    label = "clean" if clean else "mixed-exception"
    print(
        f"PASS Accounts Payable {label} {output_format.upper()} task: "
        f"task_id={task_id} artifact_id={completed.artifact_id}"
    )
    _assert_no_restricted_keys(created)
    if body and any(f'"{field}"'.encode() in body.lower() for field in AP_RESTRICTED_FIELDS):
        raise SmokeFailure("Restricted finance fields entered the AP submission response")
    return completed


def _run_ap_approval_restart(
    runtime: ComposeRuntime,
    base_url: str,
) -> CompletedTask:
    status, created, _body = _request(
        f"{base_url}/api/v1/tasks",
        method="POST",
        payload={
            "task": _ap_task_text(clean=False, output_format="json"),
            "task_type": "accounts_payable_analysis.v1",
            "output_format": "json",
            "max_steps": 14,
            "require_approval": True,
        },
    )
    task_id = created.get("task_id")
    approval_id = created.get("pending_approval_id")
    if (
        status != 202
        or created.get("status") != "WAITING_APPROVAL"
        or not isinstance(task_id, str)
        or not isinstance(approval_id, str)
    ):
        raise SmokeFailure("AP approval task did not enter WAITING_APPROVAL")
    pending_steps = _require_list(
        _json_ok(f"{base_url}/api/v1/tasks/{task_id}/steps").get("steps"),
        "Pending AP steps",
    )
    knowledge = [item for item in pending_steps if item.get("tool_name") == "knowledge_search"]
    if len(knowledge) != 1 or knowledge[0].get("attempt_count") != 1:
        raise SmokeFailure("AP knowledge work was not checkpointed before approval")

    runtime.run("restart", "copilot-api")
    _wait_for("Copilot readiness before AP approval resume", lambda: _ready(base_url))
    approval_url = f"{base_url}/api/v1/tasks/{task_id}/approvals/{approval_id}"
    approval = _json_ok(approval_url)
    proposed = _require_mapping(approval.get("proposed_arguments"), "AP approval arguments")
    if (
        approval.get("tool_name") != "database_query"
        or approval.get("status") != "PENDING"
        or proposed.get("row_limit") != 50_000
        or approval.get("editable_fields") != ["row_limit"]
    ):
        raise SmokeFailure("Restarted AP approval differs from the frozen database action")
    resolved = _json_ok(
        approval_url,
        method="POST",
        payload={"action": "approve", "reason": "Stage 11 local E2E approval"},
    )
    if resolved.get("task_status") != "COMPLETED" or resolved.get("resume_status") != "COMPLETED":
        raise SmokeFailure("AP approval did not resume to COMPLETED")
    _detail, steps, _evidence = _verify_ap_resources(base_url, task_id)
    resumed_knowledge = [item for item in steps if item.get("tool_name") == "knowledge_search"]
    if resumed_knowledge[0].get("attempt_count") != 1:
        raise SmokeFailure("AP approval resume replayed completed knowledge work")
    completed = _task_artifact(base_url, task_id, "json")
    _verify_ap_json(completed.content, clean=False)
    print(
        "PASS Accounts Payable approval restart/resume: "
        f"task_id={task_id} approval_id={approval_id} artifact_id={completed.artifact_id}"
    )
    return completed


class ComposeRuntime:
    """Run explicit Compose verification commands without echoing credentials."""

    def __init__(self, compose_file: Path, env_file: Path, *, project_name: str | None) -> None:
        if not compose_file.is_file():
            raise SmokeFailure(f"Compose file does not exist: {compose_file}")
        if not env_file.is_file():
            raise SmokeFailure(f"Environment file does not exist: {env_file}")
        self._base = [
            "docker",
            "compose",
            *(["--project-name", project_name] if project_name else []),
            "--env-file",
            str(env_file),
            "-f",
            str(compose_file),
        ]
        self._environment = _read_env_file(env_file)

    @property
    def sensitive_values(self) -> tuple[str, ...]:
        """Return configured secret values for negative leakage checks without logging them."""
        markers = ("PASSWORD", "API_KEY", "SECRET", "TOKEN")
        placeholders = ("replace", "example", "changeme", "<")
        return tuple(
            value
            for name, value in self._environment.items()
            if any(marker in name.upper() for marker in markers)
            and value
            and not any(marker in value.casefold() for marker in placeholders)
        )

    def run(
        self,
        *arguments: str,
        allowed_codes: frozenset[int] = frozenset({0}),
        environment: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [*self._base, *arguments]
        process_environment = None
        if environment:
            import os

            process_environment = dict(os.environ)
            process_environment.update(environment)
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=process_environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode not in allowed_codes:
            raise SmokeFailure(
                f"Compose command failed: {' '.join(arguments)} (exit {result.returncode})"
            )
        return result

    def inspect_service(self, service: str) -> Mapping[str, Any]:
        """Return Docker inspection data without printing expanded service environment."""
        container_id = self.run("ps", "-q", service).stdout.strip()
        if not container_id:
            raise SmokeFailure(f"Compose service has no running container: {service}")
        result = subprocess.run(
            ["docker", "inspect", container_id],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise SmokeFailure(f"Docker could not inspect running service: {service}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise SmokeFailure("Docker inspection returned invalid JSON") from error
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise SmokeFailure("Docker inspection returned an unexpected shape")
        return payload[0]

    def psql(
        self,
        service: str,
        sql: str,
        *,
        runtime_reader: bool,
        allowed_codes: frozenset[int] = frozenset({0}),
    ) -> subprocess.CompletedProcess[str]:
        if runtime_reader:
            user = self._environment.get("BUSINESS_READONLY_USER", "quality_readonly")
            password = self._environment.get(
                "BUSINESS_READONLY_PASSWORD", "quality_readonly_local_enterprise_password"
            )
            database = self._environment.get("BUSINESS_POSTGRES_DB", "supplier_quality")
            return self.run(
                "exec",
                "-T",
                "-e",
                "PGPASSWORD",
                service,
                "psql",
                "-h",
                "127.0.0.1",
                "-U",
                user,
                "-d",
                database,
                "-v",
                "ON_ERROR_STOP=1",
                "-Atc",
                sql,
                allowed_codes=allowed_codes,
                environment={"PGPASSWORD": password},
            )
        if service == "copilot-postgres":
            user = self._environment.get("COPILOT_POSTGRES_USER", "copilot")
            database = self._environment.get("COPILOT_POSTGRES_DB", "copilot")
        else:
            user = self._environment.get("BUSINESS_POSTGRES_ADMIN_USER", "quality_seed")
            database = self._environment.get("BUSINESS_POSTGRES_DB", "supplier_quality")
        return self.run(
            "exec",
            "-T",
            service,
            "psql",
            "-U",
            user,
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-Atc",
            sql,
            allowed_codes=allowed_codes,
        )


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", maxsplit=1)
        values[name.strip()] = value.strip().strip("\"'")
    return values


def _verify_database_boundaries(runtime: ComposeRuntime) -> None:
    select_result = runtime.psql(
        "business-postgres",
        "SELECT count(*) FROM incoming_inspections",
        runtime_reader=True,
    )
    if int(select_result.stdout.strip()) != 5_000:
        raise SmokeFailure("Runtime reader did not observe the reviewed 5,000-row dataset")
    ap_counts = runtime.psql(
        "business-postgres",
        (
            "SELECT count(*) || ':' || "
            "count(*) FILTER (WHERE tenant_id = 'TENANT-DEMO') || ':' || "
            "count(*) FILTER (WHERE tenant_id = 'TENANT-A') FROM invoices"
        ),
        runtime_reader=True,
    ).stdout.strip()
    if ap_counts != "27:25:2":
        raise SmokeFailure("Runtime reader did not observe the reviewed tenant-isolated AP seed")
    mutations = (
        "INSERT INTO suppliers (tenant_id, supplier_code, name, country, category, risk_level) "
        "VALUES ('TENANT-DEMO', 'WRITE-PROBE', 'Probe', 'CN', 'Probe', 'LOW')",
        "UPDATE suppliers SET name = name WHERE false",
        "DELETE FROM suppliers WHERE false",
        "CREATE TABLE local_enterprise_write_probe (id integer)",
    )
    for statement in mutations:
        result = runtime.psql(
            "business-postgres",
            statement,
            runtime_reader=True,
            allowed_codes=frozenset(range(1, 256)),
        )
        if result.returncode == 0:
            raise SmokeFailure("Business runtime credential unexpectedly accepted a write")

    table_query = "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
    copilot_tables = set(
        runtime.psql("copilot-postgres", table_query, runtime_reader=False).stdout.splitlines()
    )
    business_tables = set(
        runtime.psql("business-postgres", table_query, runtime_reader=False).stdout.splitlines()
    )
    required_copilot_tables = {
        "workflow_tasks",
        "workflow_evidence",
        "workflow_approvals",
        "workflow_artifacts",
        "workflow_graph_audit",
        "workflow_tool_audit",
        "checkpoints",
    }
    if not required_copilot_tables.issubset(copilot_tables):
        raise SmokeFailure("Copilot PostgreSQL lacks required persistence/checkpoint tables")
    business_source_tables = {
        "suppliers",
        "incoming_inspections",
        "legal_entities",
        "business_units",
        "purchase_orders",
        "invoices",
        "payments",
    }
    if not business_source_tables.issubset(business_tables):
        raise SmokeFailure("Business PostgreSQL lacks Supplier Quality or Accounts Payable tables")
    if any(
        table.startswith("workflow_") or table.startswith("checkpoint") for table in business_tables
    ):
        raise SmokeFailure("Business PostgreSQL contains Copilot persistence tables")
    if business_source_tables.intersection(copilot_tables):
        raise SmokeFailure("Copilot PostgreSQL contains Business Tool source tables")
    print(
        "PASS PostgreSQL isolation, AP tenant controls, and database-native read-only enforcement"
    )


def _source_name(source: str) -> str:
    return source.replace("\\", "/").rsplit("/", maxsplit=1)[-1]


def _require_formal_source(source: str) -> None:
    normalized = source.casefold()
    if any(marker in normalized for marker in FIXTURE_SOURCE_MARKERS):
        raise SmokeFailure(f"Fixture RAG source entered the formal gate: {source}")
    if _source_name(source) not in FORMAL_DOCUMENT_SOURCES:
        raise SmokeFailure(f"RAG source is outside the formal Supplier Quality corpus: {source}")


def _verify_formal_rag_identity(runtime: ComposeRuntime) -> tuple[str, str]:
    inspection = runtime.inspect_service("enterprise-rag-engine")
    config = inspection.get("Config")
    if not isinstance(config, dict):
        raise SmokeFailure("RAG container inspection lacks Config")
    configured_image = config.get("Image")
    expected_image = runtime._environment.get("ENTERPRISE_RAG_IMAGE", FORMAL_RAG_IMAGE)
    if configured_image != expected_image or any(
        marker in str(configured_image).casefold() for marker in FIXTURE_SOURCE_MARKERS
    ):
        raise SmokeFailure(
            f"RAG container image is not the configured formal image: {configured_image}"
        )
    image_id = inspection.get("Image")
    mounts = inspection.get("Mounts")
    if not isinstance(image_id, str) or not image_id:
        raise SmokeFailure("RAG container inspection lacks image ID")
    if not isinstance(mounts, list):
        raise SmokeFailure("RAG container inspection lacks mounts")
    by_destination = {
        mount.get("Destination"): mount for mount in mounts if isinstance(mount, dict)
    }
    data_mount = by_destination.get("/app/data")
    document_mount = by_destination.get("/app/enterprise-documents")
    if not isinstance(data_mount, dict) or data_mount.get("Type") != "volume":
        raise SmokeFailure("Formal RAG /app/data is not backed by a named volume")
    if not isinstance(document_mount, dict) or document_mount.get("RW") is not False:
        raise SmokeFailure("Formal RAG source documents are not mounted read-only")
    print(f"PASS formal RAG container identity: image={configured_image} image_id={image_id}")
    return str(configured_image), image_id


def _verify_formal_rag_query(runtime: ComposeRuntime) -> Mapping[str, Any]:
    trace_id = "local-formal-rag-smoke"
    query_program = (
        "import json,urllib.request;"
        "body=json.dumps({'question':'What is the approved supplier defect rate formula and "
        "quality escalation policy?'}).encode();"
        "request=urllib.request.Request('http://127.0.0.1:8000/ask',data=body,"
        "headers={'Content-Type':'application/json','X-Trace-ID':'local-formal-rag-smoke'},"
        "method='POST');"
        "print(urllib.request.urlopen(request,timeout=120).read().decode())"
    )
    result = runtime.run(
        "exec",
        "-T",
        "enterprise-rag-engine",
        "python",
        "-c",
        query_program,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SmokeFailure("Formal RAG /ask did not return JSON") from error
    if not isinstance(payload, dict):
        raise SmokeFailure("Formal RAG /ask response is not an object")
    sources = payload.get("sources")
    contexts = payload.get("contexts")
    if (
        payload.get("route") != "rag"
        or payload.get("rag_trace_id") != trace_id
        or not isinstance(sources, list)
        or not sources
        or not isinstance(contexts, list)
        or not contexts
    ):
        raise SmokeFailure("Formal RAG /ask lacks route, trace, sources, or contexts")
    observed: set[str] = set()
    for item in sources:
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        if isinstance(source, str) and source:
            _require_formal_source(source)
            observed.add(_source_name(source))
    if not observed:
        raise SmokeFailure("Formal RAG /ask returned no recognized Supplier Quality source")
    print(
        "PASS formal RAG /ask: "
        f"route=rag sources={len(sources)} contexts={len(contexts)} "
        f"rag_trace_id={trace_id} documents={','.join(sorted(observed))}"
    )
    return payload


def _verify_formal_rag_runtime(runtime: ComposeRuntime) -> None:
    _verify_formal_rag_identity(runtime)
    _verify_formal_rag_query(runtime)


def _verify_ap_policy_snapshot(runtime: ComposeRuntime) -> Mapping[str, Any]:
    inspection = runtime.inspect_service("copilot-api")
    mounts = inspection.get("Mounts")
    if not isinstance(mounts, list):
        raise SmokeFailure("Copilot container inspection lacks mounts")
    policy_mount = next(
        (
            item
            for item in mounts
            if isinstance(item, dict) and item.get("Destination") == "/app/data/policy-snapshots"
        ),
        None,
    )
    if (
        not isinstance(policy_mount, dict)
        or policy_mount.get("Type") != "volume"
        or policy_mount.get("RW") is not False
    ):
        raise SmokeFailure("Copilot AP policy snapshot is not an immutable read-only volume mount")
    program = (
        "import json,pathlib;"
        "root=pathlib.Path('/app/data/policy-snapshots/TENANT-DEMO');"
        "current=json.loads((root/'current.json').read_text());"
        "directory=root/current['snapshot_id'];"
        "snapshot=json.loads((directory/'snapshot.json').read_text());"
        "lines=(directory/'documents.jsonl').read_text().splitlines();"
        "print(json.dumps({'snapshot_id':current['snapshot_id'],"
        "'document_count':len(snapshot['documents']),'chunk_count':len(lines),"
        "'binding_count':snapshot['binding_count'],"
        "'manifest_checksum':snapshot['manifest_checksum'],"
        "'publication_checksum':snapshot['publication_checksum']}))"
    )
    result = runtime.run("exec", "-T", "copilot-api", "python", "-c", program)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SmokeFailure(
            "Published AP policy snapshot inspection returned invalid JSON"
        ) from error
    snapshot = _require_mapping(payload, "Published AP policy snapshot")
    if (
        not str(snapshot.get("snapshot_id", "")).startswith("ap-policy-")
        or snapshot.get("document_count") != 4
        or snapshot.get("chunk_count") != 8
        or snapshot.get("binding_count") != 5
        or snapshot.get("manifest_checksum")
        != "sha256:3095ebb099a2db12dffbc699cf1f65bb7d8e324d025eb701af4bf825d6adab33"
        or not str(snapshot.get("publication_checksum", "")).startswith("sha256:")
    ):
        raise SmokeFailure("Published AP policy snapshot differs from the controlled v1 bundle")
    print(
        "PASS controlled AP RAG snapshot: "
        f"snapshot_id={snapshot['snapshot_id']} documents=4 chunks=8 bindings=5"
    )
    return snapshot


def _ready(base_url: str) -> bool:
    status, payload, _body = _request(f"{base_url}/api/health/ready", timeout_seconds=5)
    return status == 200 and payload.get("accepts_tasks") is True


def _live(base_url: str) -> bool:
    status, payload, _body = _request(f"{base_url}/api/health/live", timeout_seconds=5)
    return status == 200 and payload.get("status") == "live"


def _not_ready(base_url: str, dependency: str) -> bool:
    status, payload, _body = _request(f"{base_url}/api/health/ready", timeout_seconds=5)
    dependencies = payload.get("dependencies")
    return (
        status == 503
        and isinstance(dependencies, dict)
        and dependencies.get(dependency) == "unavailable"
    )


def _verify_recovery(
    runtime: ComposeRuntime,
    base_url: str,
    completed_tasks: Sequence[CompletedTask],
    *,
    require_formal_rag: bool,
) -> None:
    runtime.run("run", "--rm", "copilot-migrate")
    print("PASS migration rerun reached the current head")

    runtime.run("restart", "copilot-api")
    _wait_for("Copilot readiness after restart", lambda: _ready(base_url))
    for completed in completed_tasks:
        _verify_durable_task(base_url, completed)
    _json_ok(f"{base_url}/api/health/ready")
    _verify_ap_policy_snapshot(runtime)
    print("PASS Copilot restart preserved both use cases, Evidence, and Artifact metadata")

    runtime.run("restart", "enterprise-rag-engine")
    _wait_for("RAG readiness after restart", lambda: _ready(base_url))
    if require_formal_rag:
        _verify_formal_rag_query(runtime)
    print("PASS RAG restart reused its existing knowledge volume without ingestion")

    runtime.run("up", "-d", "--force-recreate", "enterprise-rag-engine")
    _wait_for("RAG readiness after container replacement", lambda: _ready(base_url))
    if require_formal_rag:
        _verify_formal_rag_runtime(runtime)
    print("PASS RAG container replacement reused its existing knowledge volume")

    runtime.run("stop", "enterprise-rag-engine")
    _wait_for("RAG outage readiness", lambda: _not_ready(base_url, "rag"), timeout_seconds=120)
    if not _live(base_url):
        raise SmokeFailure("Copilot liveness failed during the RAG outage")
    runtime.run("start", "enterprise-rag-engine")
    _wait_for("RAG recovery readiness", lambda: _ready(base_url))
    if require_formal_rag:
        _verify_formal_rag_query(runtime)
    print("PASS RAG outage degraded readiness while liveness remained healthy, then recovered")

    runtime.run("stop", "business-postgres")
    _wait_for(
        "Business DB outage readiness",
        lambda: _not_ready(base_url, "business_database"),
        timeout_seconds=120,
    )
    if not _live(base_url):
        raise SmokeFailure("Copilot liveness failed during the Business DB outage")
    runtime.run("start", "business-postgres")
    _wait_for("Business DB recovery readiness", lambda: _ready(base_url))
    print(
        "PASS Business DB outage degraded readiness while liveness remained healthy, then recovered"
    )

    runtime.run("stop")
    runtime.run("start")
    _wait_for("full stack readiness after volume-preserving restart", lambda: _ready(base_url))
    for completed in completed_tasks:
        _verify_durable_task(base_url, completed)
    _verify_ap_policy_snapshot(runtime)
    print("PASS named volumes preserved both use cases and downloadable Artifact state")


def _verify_durable_task(base_url: str, completed: CompletedTask) -> None:
    task_url = f"{base_url}/api/v1/tasks/{completed.task_id}"
    detail = _json_ok(task_url)
    evidence = _json_ok(f"{task_url}/evidence")
    artifacts = _require_list(
        _json_ok(f"{task_url}/artifacts").get("artifacts"),
        "Restored Task Artifacts",
    )
    if detail.get("status") != "COMPLETED" or not evidence.get("evidence"):
        raise SmokeFailure("Task or Evidence was not durable across service restart")
    artifact = next(
        (item for item in artifacts if item.get("artifact_id") == completed.artifact_id),
        None,
    )
    if not isinstance(artifact, dict) or artifact.get("checksum") != completed.checksum:
        raise SmokeFailure("Artifact metadata was not durable across service restart")
    restored = _download_and_verify(
        f"{task_url}/artifacts/{completed.artifact_id}",
        completed.checksum,
    )
    if restored != completed.content:
        raise SmokeFailure("Artifact bytes changed across service restart")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument(
        "--project-name",
        help="Optional isolated Compose project name used by runtime/recovery checks.",
    )
    parser.add_argument(
        "--require-formal-rag",
        action="store_true",
        help="Require the formal image identity and five-document Supplier Quality corpus.",
    )
    parser.add_argument(
        "--with-runtime-checks",
        action="store_true",
        help="Also mutate service availability and verify DB security, migration, and recovery.",
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        help="Write a safe Stage 11 run manifest containing IDs and checksums only.",
    )
    parser.add_argument(
        "--planner-provider-label",
        default="unspecified",
        help="Safe non-secret planner-provider label recorded in the run manifest.",
    )
    return parser


def _write_report(
    path: Path,
    completed_tasks: Sequence[CompletedTask],
    *,
    formal_rag: bool,
    runtime_checks: bool,
    planner_provider_label: str,
) -> None:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    )
    payload = {
        "schema_version": "accounts-payable-stage11-e2e-report.v1",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "code_revision": revision,
        "worktree_dirty": dirty,
        "formal_supplier_rag": formal_rag,
        "runtime_recovery_checks": runtime_checks,
        "planner_provider": planner_provider_label,
        "external_planner_path_verified": False,
        "ap_dataset_checksum": ("e920b4b13403831b0c4e7150edea452736f5c278cb2ed272b98c25da66b02f91"),
        "ap_policy_manifest_checksum": (
            "sha256:3095ebb099a2db12dffbc699cf1f65bb7d8e324d025eb701af4bf825d6adab33"
        ),
        "tasks": [
            {
                "task_id": item.task_id,
                "trace_id": item.trace_id,
                "artifact_id": item.artifact_id,
                "artifact_checksum": item.checksum,
                "format": item.output_format.upper(),
            }
            for item in completed_tasks
        ],
        "checks": [
            "supplier_quality_json_pdf",
            "accounts_payable_clean_json",
            "accounts_payable_mixed_json_pdf",
            "accounts_payable_approval_restart" if runtime_checks else "approval_restart_not_run",
            (
                "postgres_select_only_and_tenant_isolation"
                if runtime_checks
                else "db_boundary_not_run"
            ),
            "artifact_download_checksum",
            "restricted_finance_field_absence",
            "configured_secret_absence",
            "volume_preserving_restart" if runtime_checks else "restart_not_run",
        ],
    }
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"PASS safe Stage 11 run manifest: {destination}")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    base_url = arguments.base_url.rstrip("/")
    try:
        runtime = None
        if arguments.require_formal_rag or arguments.with_runtime_checks:
            runtime = ComposeRuntime(
                arguments.compose_file.resolve(),
                arguments.env_file.resolve(),
                project_name=arguments.project_name,
            )
        _wait_for("browser-facing frontend", lambda: _ready(base_url))
        print("PASS frontend reverse proxy and Copilot readiness")
        if arguments.require_formal_rag:
            assert runtime is not None
            _verify_formal_rag_runtime(runtime)
        if runtime is not None:
            _verify_ap_policy_snapshot(runtime)
        completed_tasks = [
            _run_supplier_task(
                base_url,
                "json",
                require_formal_rag=arguments.require_formal_rag,
            ),
            _run_supplier_task(
                base_url,
                "pdf",
                require_formal_rag=arguments.require_formal_rag,
            ),
            _run_ap_task(base_url, "json", clean=True),
            _run_ap_task(base_url, "json", clean=False),
            _run_ap_task(base_url, "pdf", clean=False),
        ]
        if runtime is not None:
            _assert_no_secret_material(
                [item.content for item in completed_tasks],
                runtime.sensitive_values,
            )
            print("PASS configured secret values are absent from downloaded Artifacts")
        if arguments.with_runtime_checks:
            assert runtime is not None
            approval_task = _run_ap_approval_restart(runtime, base_url)
            completed_tasks.append(approval_task)
            _assert_no_secret_material(
                [item.content for item in completed_tasks],
                runtime.sensitive_values,
            )
            _verify_database_boundaries(runtime)
            _verify_recovery(
                runtime,
                base_url,
                (completed_tasks[0], completed_tasks[3], approval_task),
                require_formal_rag=arguments.require_formal_rag,
            )
        if arguments.report_output is not None:
            _write_report(
                arguments.report_output,
                completed_tasks,
                formal_rag=arguments.require_formal_rag,
                runtime_checks=arguments.with_runtime_checks,
                planner_provider_label=arguments.planner_provider_label,
            )
    except (OSError, ValueError, SmokeFailure) as error:
        print(f"FAIL Local Enterprise E2E: {error}", file=sys.stderr)
        return 1
    print("PASS Local Enterprise E2E smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
