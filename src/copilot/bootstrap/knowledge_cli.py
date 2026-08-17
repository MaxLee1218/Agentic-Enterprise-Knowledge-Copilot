"""Executable composition for standalone Enterprise RAG verification commands."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from copilot.bootstrap.knowledge import build_http_knowledge_client
from copilot.config import ConfigurationError, get_settings
from copilot.contracts import EvidenceItem, JsonObject, ToolCall
from copilot.evidence.ledger import InMemoryEvidenceLedger
from copilot.persistence.audit_repository import ToolAuditRepository
from copilot.policies.offline import OfflineSupplierQualityAuthorizer
from copilot.services.execution import ExecutionContext
from copilot.tools import ToolExecutor, ToolRegistry
from copilot.tools.cancellation import CancellationToken
from copilot.tools.knowledge import (
    KnowledgeResult,
    KnowledgeTool,
    MockKnowledgeClient,
    RAGAuthenticationError,
    RAGError,
    RAGInternalError,
    RAGInvalidResponseError,
    RAGTimeoutError,
    RAGUnavailableError,
)

EXIT_CONFIGURATION = 2
EXIT_UNAVAILABLE = 3
EXIT_TIMEOUT = 4
EXIT_AUTHENTICATION = 5
EXIT_INVALID_RESPONSE = 6
EXIT_INTERNAL = 7


def health_main(argv: Sequence[str] | None = None) -> int:
    """Run the standalone RAG health command."""
    parser = argparse.ArgumentParser(description="Check Enterprise RAG GET /health.")
    _common_arguments(parser)
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
        with build_http_knowledge_client(
            settings,
            base_url=args.base_url,
            timeout_seconds=args.timeout,
        ) as client:
            result = client.health_check(trace_id=args.trace_id)
    except (ConfigurationError, ValueError) as exc:
        return _print_configuration_error(exc, args.json)
    except RAGError as exc:
        if args.debug:
            raise
        return _print_rag_error(exc, args.json)
    payload = result.model_dump(mode="json")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"healthy: {result.healthy}")
        print(f"status: {result.status}")
        print(f"latency_ms: {result.latency_ms}")
        print(f"rag_trace_id: {result.rag_trace_id}")
    return 0


def ask_main(argv: Sequence[str] | None = None) -> int:
    """Run the standalone RAG ask command, optionally showing governed Evidence."""
    parser = argparse.ArgumentParser(description="Call Enterprise RAG POST /ask.")
    _common_arguments(parser)
    parser.add_argument("--question", required=True, help="Non-blank question sent to RAG.")
    parser.add_argument(
        "--show-evidence",
        action="store_true",
        help="Normalize sources through KnowledgeTool and print recorded EvidenceItem objects.",
    )
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
        with build_http_knowledge_client(
            settings,
            base_url=args.base_url,
            timeout_seconds=args.timeout,
        ) as client:
            result = client.ask(args.question, trace_id=args.trace_id)
        evidence = _record_evidence(result, args.question) if args.show_evidence else ()
    except (ConfigurationError, ValueError) as exc:
        return _print_configuration_error(exc, args.json)
    except RAGError as exc:
        if args.debug:
            raise
        return _print_rag_error(exc, args.json)

    payload: dict[str, Any] = {
        "answer": result.answer,
        "sources": [source.model_dump(mode="json") for source in result.sources],
        "contexts": [context.model_dump(mode="json") for context in result.contexts],
        "contexts_count": len(result.contexts),
        "route": result.route,
        "latency_ms": result.latency_ms,
        "rag_trace_id": result.rag_trace_id,
    }
    if args.show_evidence:
        payload["evidence"] = [item.model_dump(mode="json") for item in evidence]
        payload["evidence_count"] = len(evidence)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"answer: {result.answer}")
        print(f"sources: {len(result.sources)}")
        for index, source in enumerate(result.sources, start=1):
            print(f"  [{index}] {source.source}")
        print(f"contexts_count: {len(result.contexts)}")
        print(f"route: {result.route}")
        print(f"latency_ms: {result.latency_ms}")
        print(f"rag_trace_id: {result.rag_trace_id}")
        if args.show_evidence:
            print(f"Evidence count: {len(evidence)}")
            for index, item in enumerate(evidence, start=1):
                reference = item.source_reference.reference.root
                print(f"\n[{index}]")
                print(f"type: {item.source_type.value}")
                print(f"source: {reference.get('source', reference.get('document_id'))}")
                print(f"page: {reference.get('page', 'unknown')}")
                print(f"chunk_id: {reference.get('chunk_id', 'unknown')}")
                print(f"rag_trace_id: {reference.get('rag_trace_id')}")
    return 0


def warmup_main(argv: Sequence[str] | None = None) -> int:
    """Load the live RAG query path while emitting only bounded readiness metadata."""
    parser = argparse.ArgumentParser(description="Warm Enterprise RAG POST /ask dependencies.")
    _common_arguments(parser)
    parser.add_argument(
        "--question",
        default="supplier quality policy defect thresholds",
        help="Non-blank warmup question sent to the approved local RAG service.",
    )
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
        with build_http_knowledge_client(
            settings,
            base_url=args.base_url,
            timeout_seconds=args.timeout,
        ) as client:
            result = client.ask(args.question, trace_id=args.trace_id)
    except (ConfigurationError, ValueError) as exc:
        return _print_configuration_error(exc, args.json)
    except RAGError as exc:
        if args.debug:
            raise
        return _print_rag_error(exc, args.json)

    payload = {
        "warmed": True,
        "source_count": len(result.sources),
        "context_count": len(result.contexts),
        "latency_ms": result.latency_ms,
        "rag_trace_id": result.rag_trace_id,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")
    return 0


def warmup_entrypoint() -> int:
    """Console-script adapter for deployment readiness composition."""
    return warmup_main()


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", help="Override Settings.RAG_BASE_URL.")
    parser.add_argument("--timeout", type=float, help="Override Settings.RAG_TIMEOUT_SECONDS.")
    parser.add_argument("--trace-id", help="Forward this trace identifier.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit one machine-readable JSON object.",
    )
    parser.add_argument("--debug", action="store_true", help="Show a traceback on failure.")


def _record_evidence(result: KnowledgeResult, question: str) -> tuple[EvidenceItem, ...]:
    client = MockKnowledgeClient(ask_result=result)
    tool = KnowledgeTool(client)
    registry = ToolRegistry()
    registry.register(tool)
    ledger = InMemoryEvidenceLedger()
    executor = ToolExecutor(
        registry=registry,
        authorizer=OfflineSupplierQualityAuthorizer(),
        evidence_recorder=ledger,
        audit_sink=ToolAuditRepository(),
    )
    call = ToolCall(
        tool_call_id="TC-KNOWLEDGE-CLI",
        task_id="T-KNOWLEDGE-CLI",
        step_id="S-KNOWLEDGE-CLI",
        tool_name=tool.definition.tool_name,
        tool_version=tool.definition.tool_version,
        input=JsonObject(
            {
                "query": question.strip(),
                "tenant_id": "local-verification",
                "collection_ids": ["manual-verification"],
                "supplier_ids": [],
                "date_range": {"start": "1970-01-01", "end": "9999-12-31"},
                "top_k": 20,
                "index_snapshot_id": "live-rag-verification",
            }
        ),
        idempotency_key="knowledge-cli-verification",
        approval_id=None,
        deadline_at=datetime.now(UTC) + timedelta(seconds=25),
        tenant_id="local-verification",
        user_id="local-verification",
    )
    try:
        execution = executor.execute(
            call,
            ExecutionContext(
                task_id=call.task_id,
                trace_id="TRACE-KNOWLEDGE-CLI",
                step_id=call.step_id,
                user_id=call.user_id,
                tenant_id=call.tenant_id,
                roles=("quality_analyst",),
                scopes=("task:execute", "data:quality.v1"),
                data_scope=("quality.v1",),
                purpose="supplier_quality_analysis.v1",
                authentication_source="explicit_local_verification",
                is_demo_identity=True,
                authenticated=True,
                deadline_at=call.deadline_at,
                approval_required=False,
                approval_id=None,
                cancellation=CancellationToken(),
            ),
        )
    finally:
        executor.close()
    if execution.error is not None:
        raise RuntimeError(execution.error.message)
    return ledger.list_for_call(
        call.tool_call_id,
        task_id=call.task_id,
        tenant_id=call.tenant_id,
    )


def _print_configuration_error(error: Exception, as_json: bool) -> int:
    payload = {"error_type": "ConfigurationError", "message": str(error)}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"ConfigurationError: {error}")
    return EXIT_CONFIGURATION


def _print_rag_error(error: RAGError, as_json: bool) -> int:
    payload = {
        "error_type": type(error).__name__,
        "message": error.message,
        "trace_id": error.trace_id,
        "status_code": error.status_code,
        "attempts": error.attempts,
        "retryable": error.retryable,
        "safe_details": error.safe_details,
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{type(error).__name__}: {error.message}")
        print(f"trace_id: {error.trace_id}")
        print(f"attempts: {error.attempts}")
    if isinstance(error, RAGUnavailableError):
        return EXIT_UNAVAILABLE
    if isinstance(error, RAGTimeoutError):
        return EXIT_TIMEOUT
    if isinstance(error, RAGAuthenticationError):
        return EXIT_AUTHENTICATION
    if isinstance(error, RAGInvalidResponseError):
        return EXIT_INVALID_RESPONSE
    if isinstance(error, RAGInternalError):
        return EXIT_INTERNAL
    return EXIT_INTERNAL


__all__ = ["ask_main", "health_main", "warmup_entrypoint", "warmup_main"]
