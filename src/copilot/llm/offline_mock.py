"""Offline structured provider for local natural-language demonstrations and smoke tests."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from time import perf_counter
from typing import TypeVar, cast

from pydantic import BaseModel, JsonValue

from copilot.contracts import (
    APExceptionType,
    ArtifactType,
    CapabilityName,
    JsonObject,
    MoneyThreshold,
    ProposedPlan,
    ProposedStep,
    ReportLanguage,
    RetryPolicy,
    StepType,
    TaskPlan,
    TaskStep,
    TaskType,
)
from copilot.llm.schemas import (
    APDateRangeCandidate,
    APDeliverableCandidate,
    APTaskUnderstandingOutput,
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
from copilot.services.workflows.accounts_payable_plan import (
    AGGREGATE_EXCEPTION_SUMMARY,
    AGGREGATE_SUPPLIER_RATE,
    AP_DETECTION_BINDINGS,
    AP_POPULATION_SUFFIX,
    GENERATE_AP_REPORT,
    RETRIEVE_AP_POLICY,
)

TModel = TypeVar("TModel", bound=BaseModel)
_YEAR = re.compile(r"(?<!\d)(20\d{2}|[3-9]\d{3})(?!\d)")
_QUARTER = re.compile(r"\bQ([1-4])\b", re.IGNORECASE)
_GERMAN_QUARTER = re.compile(r"\b([1-4])\.\s*Quartal\b", re.IGNORECASE)
_SUPPLIER = re.compile(r"\b(?:SUP|S)-[A-Za-z0-9][A-Za-z0-9_-]*\b", re.IGNORECASE)
_LEGAL_ENTITY = re.compile(r"\bLE-[A-Za-z0-9][A-Za-z0-9_-]*\b", re.IGNORECASE)
_BUSINESS_UNIT = re.compile(r"\bBU-[A-Za-z0-9][A-Za-z0-9_-]*\b", re.IGNORECASE)
_ISO_DATE = re.compile(r"(?<!\d)(20\d{2})-(0[1-9]|1[0-2])-([0-2]\d|3[01])(?!\d)")
_CURRENCY = re.compile(r"\b[A-Z]{3}\b")
_CHINESE_QUARTERS = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}


def _clarification_input(payload: dict[str, object]) -> tuple[dict[str, object], str]:
    """Keep the original request separate while exposing validated and latest response data."""
    original = str(payload["untrusted_user_input"])
    raw_context = payload.get("validated_clarification_context")
    context = dict(raw_context) if isinstance(raw_context, dict) else {}
    latest = payload.get("untrusted_latest_clarification")
    parts = [original]
    if context:
        parts.append(json.dumps(context, ensure_ascii=False, sort_keys=True))
    if isinstance(latest, dict):
        message = latest.get("message")
        answers = latest.get("answers")
        if isinstance(message, str):
            parts.append(message)
        if isinstance(answers, dict):
            parts.append(json.dumps(answers, ensure_ascii=False, sort_keys=True))
    return context, "\n".join(parts)


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
        elif output_schema is APTaskUnderstandingOutput:
            parsed = cast(TModel, self._understand_accounts_payable(payload))
        elif output_schema is ProposedPlan:
            parsed = cast(TModel, self._propose(payload))
        elif output_schema is TaskPlan:
            parsed = cast(TModel, self._plan(payload, context.node_name))
        else:
            raise LLMSchemaValidationError(
                f"Offline mock does not support schema {output_schema.__name__}"
            )
        serialized = parsed.model_dump_json()
        from copilot.llm.structured_output import structured_output_fingerprint

        raw_output_chars, raw_output_hash = structured_output_fingerprint(serialized)
        return StructuredLLMResult[TModel](
            parsed_output=parsed,
            provider="mock",
            model="offline-governed-domains-v2",
            latency_ms=max(0, round((perf_counter() - started) * 1000)),
            usage=LLMUsage(),
            finish_reason="stop",
            request_id=f"offline-{context.task_id}-{context.node_name}",
            attempts=1,
            raw_output_chars=raw_output_chars,
            raw_output_hash=raw_output_hash,
        )

    @staticmethod
    def _propose(payload: dict[str, object]) -> ProposedPlan:
        """Return the shared semantic flow; domain expansion belongs to PlanCompiler."""
        context = cast(dict[str, object], payload.get("task_context", {}))
        task_type = str(context.get("task_type", "supplier_quality_analysis.v1"))
        output = cast(dict[str, object], context.get("output", {}))
        report_arguments: dict[str, JsonValue] = (
            {"format": str(output["artifact_type"])} if output else {}
        )
        domain_label = (
            "Accounts Payable"
            if task_type == TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1.value
            else "supplier quality"
        )
        return ProposedPlan(
            steps=(
                ProposedStep(
                    step_id="knowledge",
                    capability=CapabilityName.KNOWLEDGE_SEARCH,
                    purpose=f"Retrieve controlled policy evidence for {domain_label}",
                ),
                ProposedStep(
                    step_id="database",
                    capability=CapabilityName.DATABASE_QUERY,
                    purpose=f"Retrieve governed {domain_label} business data",
                ),
                ProposedStep(
                    step_id="analysis",
                    capability=CapabilityName.ANALYSIS_ENGINE,
                    purpose=f"Calculate deterministic {domain_label} findings",
                    depends_on=("database",),
                ),
                ProposedStep(
                    step_id="report",
                    capability=CapabilityName.REPORT_GENERATOR,
                    purpose="Generate the requested internal evidence-backed report",
                    arguments=JsonObject(report_arguments),
                    depends_on=("knowledge", "analysis"),
                ),
            )
        )

    @staticmethod
    def _understand(payload: dict[str, object]) -> TaskUnderstandingOutput:
        original = str(payload["untrusted_user_input"])
        context, raw = _clarification_input(payload)
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
        if isinstance(context.get("year"), int) and isinstance(context.get("quarter"), int):
            year = int(cast(int, context["year"]))
            quarter = int(cast(int, context["quarter"]))
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
            goal=original[:2000],
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
        if contract_raw.get("task_type") == TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1.value:
            return OfflineMockLLM._plan_accounts_payable(payload, node_name)
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
                tool_version=str(tools[name]["tool_version"]),
                contract_profile=str(tools[name]["contract_profile"]),
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

    @staticmethod
    def _understand_accounts_payable(
        payload: dict[str, object],
    ) -> APTaskUnderstandingOutput:
        original = str(payload["untrusted_user_input"])
        context, raw = _clarification_input(payload)
        trusted = cast(dict[str, object], payload["trusted_context"])
        dates = [date.fromisoformat(match.group(0)) for match in _ISO_DATE.finditer(raw)]
        start_date: date | None = None
        end_date: date | None = None
        if len(dates) >= 2:
            start_date, end_date = dates[0], dates[1]
        else:
            year_match = _YEAR.search(raw)
            quarter_match = _QUARTER.search(raw) or _GERMAN_QUARTER.search(raw)
            quarter = int(quarter_match.group(1)) if quarter_match is not None else None
            if quarter is None:
                chinese = re.search(r"第([一二三四1234])季度", raw)
                if chinese is not None:
                    quarter = _CHINESE_QUARTERS[chinese.group(1)]
            if year_match is not None and quarter is not None:
                year = int(year_match.group(1))
                month = (quarter - 1) * 3 + 1
                start_date = date(year, month, 1)
                end_month = month + 2
                next_month = date(year + (end_month // 12), end_month % 12 + 1, 1)
                end_date = date.fromordinal(next_month.toordinal() - 1)
        latest = payload.get("untrusted_latest_clarification")
        latest_answers = latest.get("answers") if isinstance(latest, dict) else None
        range_answer = (
            latest_answers.get("time_range") if isinstance(latest_answers, dict) else None
        )
        if isinstance(range_answer, dict):
            answer_start = range_answer.get("start_date")
            answer_end = range_answer.get("end_date")
            if isinstance(answer_start, str) and isinstance(answer_end, str):
                start_date = date.fromisoformat(answer_start)
                end_date = date.fromisoformat(answer_end)
        context_start = context.get("start_date")
        context_end = context.get("end_date")
        if isinstance(context_start, str) and isinstance(context_end, str):
            start_date = date.fromisoformat(context_start)
            end_date = date.fromisoformat(context_end)
        missing = (
            ()
            if start_date is not None and end_date is not None
            else ("An explicit Accounts Payable date range is required",)
        )
        folded = raw.casefold()
        exception_types: list[APExceptionType] = []
        keyword_map = (
            (APExceptionType.EXACT_DUPLICATE_INVOICE, ("duplicate", "重复")),
            (APExceptionType.PO_AMOUNT_VARIANCE, ("po variance", "po差异", "采购订单差异")),
            (APExceptionType.MISSING_REQUIRED_PO, ("missing po", "无po", "缺少po")),
            (APExceptionType.LATE_PAYMENT, ("late payment", "逾期付款", "延迟付款")),
            (
                APExceptionType.MATERIAL_EARLY_PAYMENT,
                ("early payment", "提前付款"),
            ),
            (APExceptionType.OVERPAYMENT, ("overpayment", "超额付款", "多付")),
        )
        for exception_type, keywords in keyword_map:
            if any(keyword in folded for keyword in keywords):
                exception_types.append(exception_type)
        requested_materiality: list[MoneyThreshold] = []
        for currency, amount in re.findall(
            r"\b([A-Z]{3})\s*(?:materiality|threshold)?\s*([0-9]+(?:\.[0-9]+)?)",
            raw,
            re.IGNORECASE,
        ):
            requested_materiality.append(
                MoneyThreshold(currency=currency.upper(), amount=Decimal(amount))
            )
        configured_format = trusted.get("output_format")
        if configured_format is not None:
            artifact_type = ArtifactType(str(configured_format))
        elif "json" in folded:
            artifact_type = ArtifactType.ACCOUNTS_PAYABLE_REPORT_JSON
        else:
            artifact_type = ArtifactType.ACCOUNTS_PAYABLE_REPORT_PDF
        language = (
            ReportLanguage.ZH_CN
            if any("\u4e00" <= character <= "\u9fff" for character in raw)
            else ReportLanguage.EN_US
        )
        authorized_currencies = {
            str(item) for item in cast(list[object], trusted.get("authorized_currency_scope", []))
        }
        currencies = tuple(
            dict.fromkeys(
                item for item in _CURRENCY.findall(raw.upper()) if item in authorized_currencies
            )
        )
        legal_entities = tuple(dict.fromkeys(item.upper() for item in _LEGAL_ENTITY.findall(raw)))
        context_entities = context.get("legal_entity_ids")
        if isinstance(context_entities, list) and all(
            isinstance(item, str) for item in context_entities
        ):
            legal_entities = tuple(str(item).upper() for item in context_entities)
        return APTaskUnderstandingOutput(
            goal=original[:2000],
            task_type=TaskType.ACCOUNTS_PAYABLE_ANALYSIS_V1,
            time_range=APDateRangeCandidate(start_date=start_date, end_date=end_date),
            requested_supplier_ids=tuple(
                dict.fromkeys(item.upper() for item in _SUPPLIER.findall(raw))
            ),
            requested_legal_entity_ids=legal_entities,
            requested_business_unit_ids=tuple(
                dict.fromkeys(item.upper() for item in _BUSINESS_UNIT.findall(raw))
            ),
            currency_scope=currencies,
            exception_types=tuple(exception_types),
            requested_materiality=tuple(requested_materiality),
            deliverable=APDeliverableCandidate(
                artifact_type=artifact_type,
                language=language,
            ),
            include_policy_comparison="without policy comparison" not in folded,
            missing_information=missing,
        )

    @staticmethod
    def _plan_accounts_payable(payload: dict[str, object], node_name: str) -> TaskPlan:
        contract = cast(dict[str, object], payload["trusted_task_contract"])
        task_id = str(contract["task_id"])
        manifest = cast(dict[str, object], payload["trusted_tool_manifest"])
        tools = {
            str(item["name"]): item for item in cast(list[dict[str, object]], manifest["tools"])
        }
        constraints = cast(dict[str, object], contract["constraints"])
        requested = {
            APExceptionType(str(item))
            for item in cast(list[object], constraints["exception_types"])
        }
        selected = tuple(
            binding
            for binding in AP_DETECTION_BINDINGS
            if requested.intersection(binding.exception_types)
        )
        raw_version = payload.get("trusted_next_version", 1)
        if not isinstance(raw_version, int):
            raise LLMSchemaValidationError("Offline mock received an invalid plan version")
        version = raw_version if node_name == "replan" else 1

        def ids(suffix: str) -> str:
            return f"{task_id}:{suffix}"

        retry_read = RetryPolicy(
            max_attempts=3,
            backoff_seconds=(1, 2),
            retryable_error_codes=("DATABASE_UNAVAILABLE", "DATABASE_TIMEOUT"),
        )
        retry_knowledge = RetryPolicy(
            max_attempts=3,
            backoff_seconds=(1, 2),
            retryable_error_codes=("KNOWLEDGE_UNAVAILABLE", "KNOWLEDGE_TIMEOUT"),
        )
        retry_analysis = RetryPolicy(
            max_attempts=2,
            backoff_seconds=(1,),
            retryable_error_codes=("ANALYSIS_ENGINE_FAILURE", "ANALYSIS_TIMEOUT"),
        )
        retry_report = RetryPolicy(
            max_attempts=2,
            backoff_seconds=(1,),
            retryable_error_codes=("REPORT_GENERATION_FAILURE", "REPORT_TIMEOUT"),
        )

        def step(
            suffix: str,
            name: str,
            step_type: StepType,
            dependencies: tuple[str, ...],
            retry_policy: RetryPolicy,
        ) -> TaskStep:
            entry = tools[name]
            return TaskStep(
                step_id=ids(suffix),
                task_id=task_id,
                step_type=step_type,
                tool_name=name,
                tool_version=str(entry["tool_version"]),
                contract_profile=str(entry["contract_profile"]),
                input_schema=JsonObject(cast(dict[str, JsonValue], entry["input_schema"])),
                output_schema=JsonObject(cast(dict[str, JsonValue], entry["output_schema"])),
                dependency=dependencies,
                retry_policy=retry_policy,
            )

        knowledge = step(
            RETRIEVE_AP_POLICY,
            "knowledge_search",
            StepType.KNOWLEDGE_SEARCH,
            (),
            retry_knowledge,
        )
        population = step(
            AP_POPULATION_SUFFIX,
            "database_query",
            StepType.DATABASE_QUERY,
            (),
            retry_read,
        )
        database_steps: dict[str, TaskStep] = {}
        for binding in selected:
            database_steps.setdefault(
                binding.database_suffix,
                step(
                    binding.database_suffix,
                    "database_query",
                    StepType.DATABASE_QUERY,
                    (),
                    retry_read,
                ),
            )
        detections = tuple(
            step(
                binding.analysis_suffix,
                "analysis_engine",
                StepType.ANALYSIS,
                (population.step_id, ids(binding.database_suffix)),
                retry_analysis,
            )
            for binding in selected
        )
        detection_ids = tuple(item.step_id for item in detections)
        summary = step(
            AGGREGATE_EXCEPTION_SUMMARY,
            "analysis_engine",
            StepType.ANALYSIS,
            (population.step_id, *detection_ids),
            retry_analysis,
        )
        supplier_rate = step(
            AGGREGATE_SUPPLIER_RATE,
            "analysis_engine",
            StepType.ANALYSIS,
            (population.step_id, *detection_ids),
            retry_analysis,
        )
        report_suffix = (
            f"{GENERATE_AP_REPORT}-v{version}" if node_name == "replan" else GENERATE_AP_REPORT
        )
        report = step(
            report_suffix,
            "report_generator",
            StepType.REPORT_GENERATION,
            (knowledge.step_id, summary.step_id, supplier_rate.step_id),
            retry_report,
        )
        return TaskPlan(
            task_id=task_id,
            steps=(
                knowledge,
                population,
                *database_steps.values(),
                *detections,
                summary,
                supplier_rate,
                report,
            ),
            planning_version=version,
        )


__all__ = ["OfflineMockLLM"]
