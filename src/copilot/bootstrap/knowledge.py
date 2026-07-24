"""Composition helpers for live Enterprise RAG resources."""

from __future__ import annotations

from collections.abc import Callable
from time import sleep

import httpx

from copilot.config import Settings
from copilot.tools.knowledge import HttpKnowledgeClient


def build_http_knowledge_client(
    settings: Settings,
    *,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    max_attempts: int | None = None,
    http_client: httpx.Client | None = None,
    sleeper: Callable[[float], None] = sleep,
) -> HttpKnowledgeClient:
    """Create a validated client without opening a network connection at import time."""
    return HttpKnowledgeClient(
        base_url=base_url or str(settings.rag_base_url),
        timeout_seconds=(
            settings.rag_timeout_seconds if timeout_seconds is None else timeout_seconds
        ),
        max_attempts=settings.rag_max_attempts if max_attempts is None else max_attempts,
        retry_base_delay_seconds=settings.rag_retry_base_delay_seconds,
        user_agent=settings.rag_user_agent,
        trace_header=settings.rag_trace_header,
        http_client=http_client,
        sleeper=sleeper,
    )


__all__ = ["build_http_knowledge_client"]
