"""Controlled external HTTP contracts for container deployment tests."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import sleep
from typing import Any

TRACE_HEADER = "X-Trace-ID"


class ContractHandler(BaseHTTPRequestHandler):
    """Serve the RAG and structured-model contracts used by production adapters."""

    server_version = "Stage17ExternalContract/1.0"

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path != "/health":
            self._write_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return
        trace_id = self.headers.get(TRACE_HEADER, "rag-validation-health")
        self._write_json(
            HTTPStatus.OK,
            {
                "status": "ok",
                "service": "stage17-external-contract-fixture",
                "version": "1.0",
                "rag_trace_id": trace_id,
            },
            trace_id=trace_id,
        )

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path == "/ask":
            self._handle_ask()
            return
        if self.path == "/chat/completions":
            self._handle_chat_completion()
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})

    def _handle_ask(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        question = payload.get("question")
        if not isinstance(question, str) or not question.strip():
            self._write_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"detail": "invalid question"})
            return
        trace_id = self.headers.get(TRACE_HEADER, "rag-validation-ask")
        self._write_json(
            HTTPStatus.OK,
            {
                "answer": "Use the approved supplier quality deviation procedure.",
                "sources": [
                    {
                        "index": 0,
                        "source": "supplier-quality-policy-v1",
                        "metadata": {
                            "document_version": "1.0",
                            "chunk_id": "policy-containment-1",
                            "classification": "INTERNAL",
                        },
                        "text_preview": "Contain defects and document corrective action.",
                    }
                ],
                "contexts": [
                    {
                        "content": "Contain defects and document corrective action.",
                        "source": "supplier-quality-policy-v1",
                        "chunk_id": "policy-containment-1",
                        "score": 0.99,
                        "metadata": {"classification": "INTERNAL"},
                    }
                ],
                "route": "rag",
                "latency_ms": 1,
                "rag_trace_id": trace_id,
            },
            trace_id=trace_id,
        )

    def _handle_chat_completion(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        try:
            messages = payload["messages"]
            prompt = json.loads(messages[-1]["content"])
            if "untrusted_user_input" in prompt:
                if "gate6 graceful shutdown" in str(prompt["untrusted_user_input"]).casefold():
                    sleep(8)
                output = self._understanding(prompt)
            elif "trusted_task_contract" in prompt and "trusted_tool_manifest" in prompt:
                output = self._plan(prompt)
            else:
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            self._write_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"detail": "invalid prompt"})
            return
        self._write_json(
            HTTPStatus.OK,
            {
                "id": "stage17-controlled-llm",
                "model": payload.get("model", "deepseek-chat"),
                "choices": [
                    {
                        "message": {"content": json.dumps(output, separators=(",", ":"))},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (TypeError, ValueError, json.JSONDecodeError):
            self._write_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"detail": "invalid JSON"})
            return None

    @staticmethod
    def _understanding(prompt: dict[str, Any]) -> dict[str, Any]:
        trusted = prompt["trusted_context"]
        return {
            "goal": str(prompt["untrusted_user_input"])[:2000],
            "task_type": "supplier_quality_analysis.v1",
            "entities": {"supplier_ids": []},
            "time_range": {"year": 2026, "quarter": 1},
            "deliverable": {
                "artifact_type": "QUALITY_ANALYSIS_REPORT_JSON",
                "language": "en-US",
                "required_sections": [
                    "scope",
                    "quality_policy_findings",
                    "supplier_quality_data",
                    "analysis_results",
                    "key_risks",
                    "recommendations",
                    "evidence_references",
                ],
            },
            "constraints": {
                "read_only": True,
                "max_steps": trusted["system_max_steps"],
                "metrics": [
                    "defect_count",
                    "inspected_count",
                    "defect_rate",
                    "period_over_period_trend",
                ],
            },
            "missing_information": [],
        }

    @staticmethod
    def _plan(prompt: dict[str, Any]) -> dict[str, Any]:
        contract = prompt["trusted_task_contract"]
        task_id = contract["task_id"]
        tools = {item["name"]: item for item in prompt["trusted_tool_manifest"]["tools"]}
        identifiers = {
            "knowledge_search": f"{task_id}:retrieve-quality-policy",
            "database_query": f"{task_id}:query-supplier-quality-data",
            "analysis_engine": f"{task_id}:analyze-supplier-quality",
            "report_generator": f"{task_id}:generate-supplier-quality-report",
        }
        step_types = {
            "knowledge_search": "KNOWLEDGE_SEARCH",
            "database_query": "DATABASE_QUERY",
            "analysis_engine": "ANALYSIS",
            "report_generator": "REPORT_GENERATION",
        }
        dependencies = {
            "knowledge_search": [],
            "database_query": [],
            "analysis_engine": [identifiers["database_query"]],
            "report_generator": [
                identifiers["knowledge_search"],
                identifiers["analysis_engine"],
            ],
        }
        retry = {
            "knowledge_search": ["KNOWLEDGE_UNAVAILABLE", "KNOWLEDGE_TIMEOUT"],
            "database_query": ["DATABASE_UNAVAILABLE", "DATABASE_TIMEOUT"],
            "analysis_engine": ["ANALYSIS_ENGINE_FAILURE", "ANALYSIS_TIMEOUT"],
            "report_generator": ["REPORT_GENERATION_FAILURE", "REPORT_TIMEOUT"],
        }
        steps = []
        for name in (
            "knowledge_search",
            "database_query",
            "analysis_engine",
            "report_generator",
        ):
            max_attempts = 3 if name in {"knowledge_search", "database_query"} else 2
            steps.append(
                {
                    "step_id": identifiers[name],
                    "task_id": task_id,
                    "step_type": step_types[name],
                    "tool_name": name,
                    "input_schema": tools[name]["input_schema"],
                    "output_schema": tools[name]["output_schema"],
                    "dependency": dependencies[name],
                    "retry_policy": {
                        "max_attempts": max_attempts,
                        "backoff_seconds": list(range(1, max_attempts)),
                        "retryable_error_codes": retry[name],
                    },
                }
            )
        return {"task_id": task_id, "steps": steps, "planning_version": 1}

    def log_message(self, format: str, *args: Any) -> None:
        super().log_message(format, *args)

    def _write_json(
        self,
        status: HTTPStatus,
        payload: dict[str, object],
        *,
        trace_id: str | None = None,
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if trace_id is not None:
            self.send_header(TRACE_HEADER, trace_id)
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    """Run the fixture on the Compose-internal external dependency port."""
    server = ThreadingHTTPServer(("0.0.0.0", 8000), ContractHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
