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


class SmokeFailure(RuntimeError):
    """One safe, actionable Local Enterprise verification failure."""


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


def _run_task(
    base_url: str,
    output_format: str,
    *,
    require_formal_rag: bool,
) -> tuple[str, str]:
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
        f"PASS {output_format.upper()} task: task_id={task_id} "
        f"steps={len(steps)} evidence={len(evidence)} artifact_id={artifact_id}"
    )
    return task_id, artifact_id


class ComposeRuntime:
    """Run explicit Compose verification commands without echoing credentials."""

    def __init__(self, compose_file: Path, env_file: Path) -> None:
        if not compose_file.is_file():
            raise SmokeFailure(f"Compose file does not exist: {compose_file}")
        if not env_file.is_file():
            raise SmokeFailure(f"Environment file does not exist: {env_file}")
        self._base = [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(compose_file),
        ]
        self._environment = _read_env_file(env_file)

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
    if not {"suppliers", "incoming_inspections"}.issubset(business_tables):
        raise SmokeFailure("Business PostgreSQL lacks Supplier Quality tables")
    if any(
        table.startswith("workflow_") or table.startswith("checkpoint") for table in business_tables
    ):
        raise SmokeFailure("Business PostgreSQL contains Copilot persistence tables")
    if {"suppliers", "incoming_inspections"}.intersection(copilot_tables):
        raise SmokeFailure("Copilot PostgreSQL contains Business Tool source tables")
    print("PASS PostgreSQL isolation and database-native read-only enforcement")


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
    task_id: str,
    artifact_id: str,
    *,
    require_formal_rag: bool,
) -> None:
    runtime.run("run", "--rm", "copilot-migrate")
    print("PASS migration rerun reached the current head")

    runtime.run("restart", "copilot-api")
    _wait_for("Copilot readiness after restart", lambda: _ready(base_url))
    task_url = f"{base_url}/api/v1/tasks/{task_id}"
    detail = _json_ok(task_url)
    evidence = _json_ok(f"{task_url}/evidence")
    artifacts = _json_ok(f"{task_url}/artifacts")
    if detail.get("status") != "COMPLETED" or not evidence.get("evidence"):
        raise SmokeFailure("Task or Evidence was not durable across Copilot restart")
    if not any(
        isinstance(item, dict) and item.get("artifact_id") == artifact_id
        for item in artifacts.get("artifacts", [])
    ):
        raise SmokeFailure("Artifact metadata was not durable across Copilot restart")
    artifact = next(
        item
        for item in artifacts.get("artifacts", [])
        if isinstance(item, dict) and item.get("artifact_id") == artifact_id
    )
    checksum = artifact.get("checksum")
    if not isinstance(checksum, str):
        raise SmokeFailure("Restarted Artifact metadata lacks its checksum")
    _download_and_verify(f"{task_url}/artifacts/{artifact_id}", checksum)
    _json_ok(f"{base_url}/api/health/ready")
    print("PASS Copilot restart preserved Task, Evidence, and Artifact metadata")

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
    detail = _json_ok(task_url)
    evidence = _json_ok(f"{task_url}/evidence")
    artifacts = _json_ok(f"{task_url}/artifacts")
    if detail.get("status") != "COMPLETED" or not evidence.get("evidence"):
        raise SmokeFailure("Task or Evidence was not durable across a full stack restart")
    restored_artifact: Mapping[str, Any] | None = None
    for item in artifacts.get("artifacts", []):
        if isinstance(item, dict) and item.get("artifact_id") == artifact_id:
            restored_artifact = item
            break
    if not isinstance(restored_artifact, dict) or not isinstance(
        restored_artifact.get("checksum"), str
    ):
        raise SmokeFailure("Artifact metadata was not durable across a full stack restart")
    _download_and_verify(
        f"{task_url}/artifacts/{artifact_id}",
        restored_artifact["checksum"],
    )
    print("PASS named volumes preserved Task, Evidence, and downloadable Artifact state")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    base_url = arguments.base_url.rstrip("/")
    try:
        runtime = None
        if arguments.require_formal_rag or arguments.with_runtime_checks:
            runtime = ComposeRuntime(arguments.compose_file.resolve(), arguments.env_file.resolve())
        _wait_for("browser-facing frontend", lambda: _ready(base_url))
        print("PASS frontend reverse proxy and Copilot readiness")
        if arguments.require_formal_rag:
            assert runtime is not None
            _verify_formal_rag_runtime(runtime)
        json_task_id, json_artifact_id = _run_task(
            base_url,
            "json",
            require_formal_rag=arguments.require_formal_rag,
        )
        _run_task(base_url, "pdf", require_formal_rag=arguments.require_formal_rag)
        if arguments.with_runtime_checks:
            assert runtime is not None
            _verify_database_boundaries(runtime)
            _verify_recovery(
                runtime,
                base_url,
                json_task_id,
                json_artifact_id,
                require_formal_rag=arguments.require_formal_rag,
            )
    except (OSError, ValueError, SmokeFailure) as error:
        print(f"FAIL Local Enterprise E2E: {error}", file=sys.stderr)
        return 1
    print("PASS Local Enterprise E2E smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
