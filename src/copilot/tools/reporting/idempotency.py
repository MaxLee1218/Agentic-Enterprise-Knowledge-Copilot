"""Stable Artifact command identities for crash-safe report publication."""

from __future__ import annotations

import hashlib


def artifact_id_for_idempotency_key(idempotency_key: str) -> str:
    """Derive one task-bound logical Artifact ID from the governed Tool command."""
    if not idempotency_key:
        raise ValueError("Artifact idempotency key is required")
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"A-{digest}"


__all__ = ["artifact_id_for_idempotency_key"]
