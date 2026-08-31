"""Deterministic extraction and Pydantic validation of provider output."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from hashlib import sha256
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from copilot.services.llm import (
    LLMInvalidResponseError,
    LLMResponseDiagnostics,
    LLMSchemaValidationError,
    LLMValidationIssue,
)

TModel = TypeVar("TModel", bound=BaseModel)
_JSON_FENCE = re.compile(r"\A\s*```(?:json)?\s*\n(?P<body>[\s\S]*?)\n```\s*\Z", re.IGNORECASE)
_MAX_REPAIR_RAW_CHARS = 65_536


def parse_structured_output(
    raw: str | bytes | Mapping[str, object] | BaseModel,
    output_schema: type[TModel],
) -> TModel:
    """Parse only JSON or one complete JSON fence, then validate the requested schema.

    Explanatory prose around JSON is deliberately rejected. The parser never guesses missing
    fields, rewrites values, or extracts a convenient object from arbitrary model text.
    """
    if isinstance(raw, output_schema):
        return raw
    raw_text = _raw_text(raw)
    raw_chars, raw_hash = structured_output_fingerprint(raw_text)
    if isinstance(raw, BaseModel):
        payload: object = raw.model_dump(mode="json")
    elif isinstance(raw, Mapping):
        payload = dict(raw)
    else:
        text = raw.decode("utf-8", errors="strict") if isinstance(raw, bytes) else raw
        if not text.strip():
            raise LLMInvalidResponseError(
                "LLM response was empty",
                diagnostics=LLMResponseDiagnostics(
                    raw_output_chars=raw_chars,
                    raw_output_hash=raw_hash,
                    parse_status="failed",
                ),
                raw_output=_bounded_repair_raw(raw_text),
            )
        match = _JSON_FENCE.fullmatch(text)
        if match is not None:
            text = match.group("body")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMInvalidResponseError(
                "LLM response was not a complete JSON value",
                diagnostics=LLMResponseDiagnostics(
                    raw_output_chars=raw_chars,
                    raw_output_hash=raw_hash,
                    parse_status="failed",
                ),
                raw_output=_bounded_repair_raw(raw_text),
            ) from exc
    if not isinstance(payload, dict):
        issue = LLMValidationIssue(
            field_path="root",
            error_type="object_type",
            expected="JSON object",
            received_type=type(payload).__name__,
        )
        raise LLMSchemaValidationError(
            "LLM structured output must be a JSON object",
            diagnostics=LLMResponseDiagnostics(
                raw_output_chars=raw_chars,
                raw_output_hash=raw_hash,
                parse_status="passed",
                schema_status="failed",
                validation_errors=(issue,),
            ),
            raw_output=_bounded_repair_raw(raw_text),
        )
    try:
        return output_schema.model_validate(payload)
    except ValidationError as exc:
        raw_errors = exc.errors(include_url=False)
        issues = tuple(_validation_issue(item) for item in raw_errors[:8])
        fields = sorted({item.field_path for item in issues})
        location = ", ".join(fields[:8])
        raise LLMSchemaValidationError(
            f"LLM output failed schema validation at: {location}",
            diagnostics=LLMResponseDiagnostics(
                raw_output_chars=raw_chars,
                raw_output_hash=raw_hash,
                parse_status="passed",
                schema_status="failed",
                validation_errors=issues,
            ),
            raw_output=_bounded_repair_raw(raw_text),
        ) from exc


def structured_output_fingerprint(raw: object) -> tuple[int, str]:
    """Return safe response length/hash metadata without retaining response content."""
    text = raw if isinstance(raw, str) else _raw_text(raw)
    encoded = text.encode("utf-8")
    return len(text), f"sha256:{sha256(encoded).hexdigest()}"


def _raw_text(raw: object) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, BaseModel):
        return raw.model_dump_json()
    if isinstance(raw, Mapping):
        return json.dumps(dict(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return repr(raw)


def _bounded_repair_raw(raw: str) -> str:
    return raw[:_MAX_REPAIR_RAW_CHARS]


def _validation_issue(item: Mapping[str, object]) -> LLMValidationIssue:
    location_raw = item.get("loc", ())
    location = (
        ".".join(str(part) for part in location_raw)
        if isinstance(location_raw, (tuple, list))
        else str(location_raw)
    )
    input_value = item.get("input")
    context = item.get("ctx")
    expected: str | None = None
    if isinstance(context, dict):
        safe_context = {
            str(key): value
            for key, value in context.items()
            if key in {"expected", "min_length", "max_length", "class_name"}
        }
        if safe_context:
            expected = str(safe_context)[:200]
    return LLMValidationIssue(
        field_path=(location or "root")[:500],
        error_type=str(item.get("type") or "validation_error")[:100],
        expected=expected,
        received_type=type(input_value).__name__,
    )


__all__ = ["parse_structured_output", "structured_output_fingerprint"]
