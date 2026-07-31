"""Deterministic extraction and Pydantic validation of provider output."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from copilot.services.llm import LLMInvalidResponseError, LLMSchemaValidationError

TModel = TypeVar("TModel", bound=BaseModel)
_JSON_FENCE = re.compile(r"\A\s*```(?:json)?\s*\n(?P<body>[\s\S]*?)\n```\s*\Z", re.IGNORECASE)


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
    if isinstance(raw, BaseModel):
        payload: object = raw.model_dump(mode="json")
    elif isinstance(raw, Mapping):
        payload = dict(raw)
    else:
        text = raw.decode("utf-8", errors="strict") if isinstance(raw, bytes) else raw
        if not text.strip():
            raise LLMInvalidResponseError("LLM response was empty")
        match = _JSON_FENCE.fullmatch(text)
        if match is not None:
            text = match.group("body")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMInvalidResponseError("LLM response was not a complete JSON value") from exc
    if not isinstance(payload, dict):
        raise LLMSchemaValidationError("LLM structured output must be a JSON object")
    try:
        return output_schema.model_validate(payload)
    except ValidationError as exc:
        fields = sorted(
            {
                ".".join(str(part) for part in item["loc"]) or "root"
                for item in exc.errors(include_url=False)
            }
        )
        location = ", ".join(fields[:8])
        raise LLMSchemaValidationError(
            f"LLM output failed schema validation at: {location}"
        ) from exc


__all__ = ["parse_structured_output"]
