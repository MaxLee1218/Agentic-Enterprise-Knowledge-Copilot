"""Offline structured provider for local natural-language demonstrations and smoke tests."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from time import perf_counter
from typing import TypeVar, cast

from pydantic import BaseModel, JsonValue

from copilot.contracts import (
    ArtifactType,
    JsonObject,
    ReportLanguage,
    RetryPolicy,
    StepType,
    TaskPlan,
    TaskStep,
    TaskType,
)
from copilot.llm.schemas import (
    TaskUnderstandingOutput,
    UnderstandingConstraints,
    UnderstandingDeliverable,
    UnderstandingEntities,
    UnderstandingTimeRange,
)
from copilot.services.llm import (
    LLMCallContext,
    LLMGenerationOptions,
    LLMMessage,
    LLMSchemaValidationError,
    LLMUsage,
    StructuredLLMResult,
)

TModel = TypeVar("TModel", bound=BaseModel)
_YEAR = re.compile(r"(?<!\d)(20\d{2}|[3-9]\d{3})(?!\d)")
_QUARTER = re.compile(r"\bQ([1-4])\b", re.IGNORECASE)
_GERMAN_QUARTER = re.compile(r"\b([1-4])\.\s*Quartal\b", re.IGNORECASE)
_SUPPLIER = re.compile(r"\b(?:SUP|S)-[A-Za-z0-9][A-Za-z0-9_-]*\b", re.IGNORECASE)
_CHINESE_QUARTERS = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}


class OfflineMockLLM:
    """Emulate structured model output without network access.

    This provider is intentionally limited to the frozen Supplier Quality scenario. It is used
    only when the composed API/CLI runs with ``LLM_PROVIDER=mock``.
    """

    def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        output_schema: type[TModel],
        context: LLMCallContext,
        options: LLMGenerationOptions,
    ) -> StructuredLLMResult[TModel]:
        """Return deterministic schema-valid understanding or planning output."""
        del options
        started = perf_counter()
        payload = json.loads(messages[-1].content)
        if output_schema is TaskUnderstandingOutput:
            parsed = cast(TModel, self._understand(payload))
        elif output_schema is TaskPlan:
            parsed = cast(TModel, self._plan(payload, context.node_name))
        else:
            raise LLMSchemaValidationError(
                f"Offline mock does not support schema {output_schema.__name__}"
            )
        return StructuredLLMResult[TModel](
            parsed_output=parsed,
            provider="mock",
            model="offline-supplier-quality-v1",
            latency_ms=max(0, round((perf_counter() - started) * 1000)),
            usage=LLMUsage(),
            finish_reason="stop",
            request_id=f"offline-{context.task_id}-{context.node_name}",
            attempts=1,
        )

    @staticmethod
    def _understand(payload: dict[str, object]) -> TaskUnderstandingOutput:
        raw = str(payload["untrusted_user_input"])
        trusted = cast(dict[str, object], payload["trusted_context"])
        if "markdown" in raw.casefold():
            raise LLMSchemaValidationError(
                "Markdown is outside the frozen PDF/JSON report contract"
            )
        year_match = _YEAR.search(raw)
        quarter_match = _QUARTER.search(raw) or _GERMAN_QUARTER.search(raw)
        quarter = int(quarter_match.group(1)) if quarter_match is not None else None
        if quarter is None:
            chinese = re.search(r"第([一二三四1234])季度", raw)
            if chinese is not None:
                quarter = _CHINESE_QUARTERS[chinese.group(1)]
        year = int(year_match.group(1)) if year_match is not None else None
        missing: tuple[str, ...] = ()
        if year is None or quarter is None:
            year = None
            quarter = None
            missing = ("An explicit year and quarter are required",)
        suppliers = tuple(dict.fromkeys(item.upper() for item in _SUPPLIER.findall(raw)))
        configured_format = trusted.get("output_format")
        if configured_format is not None:
            artifact_type = ArtifactType(str(configured_format))
        elif "pdf" in raw.casefold():
            artifact_type = ArtifactType.QUALITY_ANALYSIS_REPORT_PDF
        else:
            artifact_type = ArtifactType.QUALITY_ANALYSIS_REPORT_JSON
        language = (
            ReportLanguage.ZH_CN
            if any("\u4e00" <= character <= "\u9fff" for character in raw)
            else ReportLanguage.EN_US
        )
        system_max_steps = trusted["system_max_steps"]
        if not isinstance(system_max_steps, int):
            raise LLMSchemaValidationError("Offline mock received an invalid step limit")
        return TaskUnderstandingOutput(
            goal=raw[:2000],
            task_type=TaskType.SUPPLIER_QUALITY_ANALYSIS_V1,
            entities=UnderstandingEntities(supplier_ids=suppliers),
            time_range=UnderstandingTimeRange(year=year, quarter=quarter),
            deliverable=UnderstandingDeliverable(
                artifact_type=artifact_type,
                language=language,
            ),
            constraints=UnderstandingConstraints(
                read_only=True,
                max_steps=system_max_steps,
            ),
            missing_information=missing,
        )

    @staticmethod
    def _plan(payload: dict[str, object], node_name: str) -> TaskPlan:
        contract_raw = cast(dict[str, object], payload["trusted_task_contract"])
        task_id = str(contract_raw["task_id"])
        manifest_raw = cast(dict[str, object], payload["trusted_tool_manifest"])
        tools = {
            str(item["name"]): item for item in cast(list[dict[str, object]], manifest_raw["tools"])
        }
        raw_version = payload.get("trusted_next_version", contract_raw.get("contract_version", 1))
        if not isinstance(raw_version, int):
            raise LLMSchemaValidationError("Offline mock received an invalid plan version")
        version = raw_version
        if node_name in {"create_plan", "repair_plan"}:
            version = 1
        report_suffix = f"-v{version}" if node_name == "replan" else ""
        identifiers = {
            "knowledge_search": f"{task_id}:retrieve-quality-policy",
            "database_query": f"{task_id}:query-supplier-quality-data",
            "analysis_engine": f"{task_id}:analyze-supplier-quality",
            "report_generator": (f"{task_id}:generate-supplier-quality-report{report_suffix}"),
        }
        retry = {
            "knowledge_search": RetryPolicy(
                max_attempts=3,
                backoff_seconds=(1, 2),
                retryable_error_codes=("KNOWLEDGE_UNAVAILABLE", "KNOWLEDGE_TIMEOUT"),
            ),
            "database_query": RetryPolicy(
                max_attempts=3,
                backoff_seconds=(1, 2),
                retryable_error_codes=("DATABASE_UNAVAILABLE", "DATABASE_TIMEOUT"),
            ),
            "analysis_engine": RetryPolicy(
                max_attempts=2,
                backoff_seconds=(1,),
                retryable_error_codes=("ANALYSIS_ENGINE_FAILURE", "ANALYSIS_TIMEOUT"),
            ),
            "report_generator": RetryPolicy(
                max_attempts=2,
                backoff_seconds=(1,),
                retryable_error_codes=("REPORT_GENERATION_FAILURE", "REPORT_TIMEOUT"),
            ),
        }
        step_types = {
            "knowledge_search": StepType.KNOWLEDGE_SEARCH,
            "database_query": StepType.DATABASE_QUERY,
            "analysis_engine": StepType.ANALYSIS,
            "report_generator": StepType.REPORT_GENERATION,
        }
        dependencies = {
            "knowledge_search": (),
            "database_query": (),
            "analysis_engine": (identifiers["database_query"],),
            "report_generator": (
                identifiers["knowledge_search"],
                identifiers["analysis_engine"],
            ),
        }
        steps = tuple(
            TaskStep(
                step_id=identifiers[name],
                task_id=task_id,
                step_type=step_types[name],
                tool_name=name,
                input_schema=JsonObject(cast(dict[str, JsonValue], tools[name]["input_schema"])),
                output_schema=JsonObject(cast(dict[str, JsonValue], tools[name]["output_schema"])),
                dependency=dependencies[name],
                retry_policy=retry[name],
            )
            for name in (
                "knowledge_search",
                "database_query",
                "analysis_engine",
                "report_generator",
            )
        )
        return TaskPlan(task_id=task_id, steps=steps, planning_version=version)


__all__ = ["OfflineMockLLM"]
