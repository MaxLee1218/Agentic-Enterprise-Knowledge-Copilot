"""Deterministic structured-output parsing coverage."""

from collections.abc import Mapping
from typing import cast

import pytest
from pydantic import BaseModel, ConfigDict

from copilot.llm.structured_output import parse_structured_output
from copilot.services.llm import LLMInvalidResponseError, LLMSchemaValidationError


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    count: int


@pytest.mark.parametrize(
    "raw",
    [
        '{"name":"quality","count":2}',
        '  \n{"name":"quality","count":2}\n',
        '```json\n{"name":"quality","count":2}\n```',
        {"name": "quality", "count": 2},
        Output(name="quality", count=2),
    ],
)
def test_parser_accepts_only_supported_structured_wrappers(raw: object) -> None:
    supported = cast(str | bytes | Mapping[str, object] | BaseModel, raw)
    assert parse_structured_output(supported, Output) == Output(name="quality", count=2)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        '{"name":"quality"',
        'Explanation: {"name":"quality","count":2}',
        '```json\n{"name":"quality","count":2}\n```\nmore',
    ],
)
def test_parser_rejects_empty_truncated_or_explanatory_text(raw: str) -> None:
    with pytest.raises(LLMInvalidResponseError):
        parse_structured_output(raw, Output)


@pytest.mark.parametrize(
    "raw",
    [
        '{"name":"quality"}',
        '{"name":"quality","count":"many"}',
        '{"name":"quality","count":2,"extra":true}',
        "[]",
    ],
)
def test_parser_reports_schema_failures_without_echoing_payload(raw: str) -> None:
    with pytest.raises(LLMSchemaValidationError) as raised:
        parse_structured_output(raw, Output)

    assert "quality" not in str(raised.value)
