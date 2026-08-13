"""Local OpenAI-compatible generation boundary for the formal RAG retrieval gate."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class RAGGenerationHandler(BaseHTTPRequestHandler):
    """Return a deterministic cited answer without retaining received RAG context."""

    server_version = "LocalRAGGeneration/1.0"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path != "/health":
            self._write_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return
        self._write_json(HTTPStatus.OK, {"status": "ok", "service": "rag-generation-stub"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path != "/chat/completions":
            self._write_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return
        payload = self._read_json()
        if payload is None:
            return
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            self._write_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"detail": "invalid messages"})
            return
        last_message = messages[-1]
        if not isinstance(last_message, dict) or not isinstance(last_message.get("content"), str):
            self._write_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"detail": "invalid prompt"})
            return
        self._write_json(
            HTTPStatus.OK,
            {
                "id": "local-rag-generation",
                "object": "chat.completion",
                "model": payload.get("model", "local-grounded-generation"),
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": (
                                "The approved supplier-quality evidence defines the applicable "
                                "quality controls and KPI interpretation for this analysis [1]."
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
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

    def _write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        """Avoid logging prompts or document context."""


def main() -> None:
    """Serve only on the container network; Compose publishes no host port."""
    ThreadingHTTPServer(("0.0.0.0", 8000), RAGGenerationHandler).serve_forever()


if __name__ == "__main__":
    main()
