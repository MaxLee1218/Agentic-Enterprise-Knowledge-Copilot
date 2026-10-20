"""Runner controls use the old pilot facts, never V01–V20 production pre-runs."""

from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from evaluation.supplier_quality_pilot.cases import PilotCase
from evaluation.supplier_quality_pilot.contracts import PilotSnapshot
from evaluation.supplier_quality_pilot.policy import POLICY_PATH, PolicySnapshot
from evaluation.supplier_quality_pilot.source import DEFAULT_SOURCE, oracle_metrics
from evaluation.supplier_quality_validation.bundle import CaseOracle, QuarterOracle
from evaluation.supplier_quality_validation.runner import run_validation_case


def control() -> tuple[PilotSnapshot, PilotCase, CaseOracle, PolicySnapshot]:
    source = PilotSnapshot.model_validate_json(DEFAULT_SOURCE.read_bytes())
    case = PilotCase(
        case_id="P03",
        request="Analyze supplier quality for S-100 in Q2 2026 and generate a JSON report.",
        suppliers=("S-100",),
    )
    # This previously developed fixture is deliberately different from frozen validation data.
    metrics = oracle_metrics(source, case)
    keys = tuple(f"{m.supplier_id}/{m.period}/{m.metric}" for m in metrics)
    oracle = CaseOracle(
        case_id="P03",
        metrics=metrics,
        quarterly=(
            QuarterOracle(
                supplier_id="S-100",
                defect_count=60,
                inspected_count=3000,
                defect_rate=Decimal("0.0200"),
            ),
        ),
        numeric_ids=keys
        + tuple(
            f"S-100/quarter_from_report_counts/{m}"
            for m in ("defect_count", "inspected_count", "defect_rate")
        ),
        fact_ids=tuple(f"support/{key}" for key in keys),
    )
    return source, case, oracle, PolicySnapshot.model_validate_json(POLICY_PATH.read_bytes())


def test_control_task_artifact_is_scored_against_literal_answers(tmp_path: Path) -> None:
    source, case, oracle, policy = control()
    run = run_validation_case(tmp_path, source=source, case=case, oracle=oracle, policy=policy)
    assert run.qualified_delivery, [a for a in run.assertions if not a.passed]
    assert run.numeric_total == run.numeric_passed == 15
    assert run.fact_total == run.fact_passed == 12
    assert run.mode == "control"
    assert run.executed_tools == ("database_query", "analysis_engine", "report_generator")


def test_literal_oracle_cannot_be_replaced_by_derived_product_answers(tmp_path: Path) -> None:
    from decimal import Decimal

    source, case, oracle, policy = control()
    changed = oracle.model_copy(
        update={
            "metrics": (
                oracle.metrics[0].model_copy(update={"value": Decimal(987654321)}),
                *oracle.metrics[1:],
            )
        }
    )
    run = run_validation_case(tmp_path, source=source, case=case, oracle=changed, policy=policy)
    assert not run.qualified_delivery
    assert run.numeric_total == 15
    assert run.numeric_passed == 14
    assert "987654321" not in (tmp_path / "model-inputs.json").read_text()


def test_population_stop_retains_all_denominators_and_cannot_restart_elsewhere(
    tmp_path: Path,
) -> None:
    from decimal import Decimal

    import httpx
    import pytest
    from pydantic import AnyHttpUrl, SecretStr

    from copilot.config import Settings
    from evaluation.supplier_quality_pilot.budget import BudgetLedger, BudgetPolicy, BudgetStopped
    from evaluation.supplier_quality_validation.suite import ValidationReport, run_population
    from tests.integration.evaluation.test_pilot_budget import card

    source, case, oracle, policy = control()
    pricing = card()
    calls = []
    settings = Settings(
        database_url="sqlite:///:memory:",
        llm_provider="deepseek",
        llm_model="test-model",
        llm_base_url=AnyHttpUrl("https://provider.test"),
        llm_api_key=SecretStr("control-secret"),
        llm_max_output_tokens=1000,
    )
    with BudgetLedger(
        tmp_path / "ledger.db", pricing, BudgetPolicy(authorized_eur=Decimal("2.5"))
    ) as ledger:

        def respond(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200)

        def execute(directory: Path) -> ValidationReport:
            return run_population(
                directory,
                source=source,
                cases=(case,),
                oracles=(oracle,),
                policy=policy,
                freeze_hash="control-fixture",
                frozen_revision="control",
                ledger=ledger,
                settings=settings,
                transport_factory=lambda: httpx.MockTransport(respond),
            )

        report = execute(tmp_path / "population")
        assert calls == []
        assert report.partial and report.stop_reason == "TOTAL_BUDGET"
        assert report.numeric_total == 15 and report.fact_total == 12
        assert report.qualified_deliveries == 0 and report.cost_per_qualified_usd is None
        resumed = execute(tmp_path / "population")
        assert resumed == report
        with pytest.raises(BudgetStopped, match="VALIDATION_POPULATION_ALREADY_BOUND"):
            execute(tmp_path / "different")
        assert calls == []


def test_scripted_control_resumes_through_postgres_worker(tmp_path: Path) -> None:
    import os

    import pytest

    url = os.environ.get("PILOT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Dedicated migrated PILOT_TEST_POSTGRES_URL is required")
    source, case, oracle, policy = control()
    case = case.model_copy(
        update={
            "request": "Analyze supplier quality for S-100 and generate a JSON report.",
            "scripted_answer": "Q2 2026",
        }
    )
    run = run_validation_case(
        tmp_path, source=source, case=case, oracle=oracle, policy=policy, persistence_url=url
    )
    assert run.qualified_delivery, [a for a in run.assertions if not a.passed]
    assert run.interaction is not None
    assert run.interaction.initial_status == "WAITING_CLARIFICATION"
    assert run.interaction.tools_before_answer == 0
    assert run.interaction.answer_applied
    assert run.interaction.initial_task_id == run.interaction.final_task_id == run.task_id


def test_fake_provider_schema_failures_retain_every_case_and_attempt_cost(tmp_path: Path) -> None:
    import json

    import httpx
    from pydantic import AnyHttpUrl, SecretStr

    from copilot.config import Settings
    from evaluation.supplier_quality_pilot.budget import BudgetLedger, BudgetPolicy
    from evaluation.supplier_quality_validation.suite import read_report, run_population
    from tests.integration.evaluation.test_pilot_budget import card

    source, case, oracle, policy = control()
    other = case.model_copy(update={"case_id": "P04"})
    other_oracle = oracle.model_copy(update={"case_id": "P04"})
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        # Schema-invalid but definitely billed. Retries/repair stay in the first case.
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 1000,
                    "prompt_cache_hit_tokens": 200,
                    "prompt_cache_miss_tokens": 800,
                    "completion_tokens": 100,
                    "total_tokens": 1100,
                },
            },
        )

    settings = Settings(
        database_url="sqlite:///:memory:",
        llm_provider="deepseek",
        llm_model="test-model",
        llm_base_url=AnyHttpUrl("https://provider.test"),
        llm_api_key=SecretStr("control-secret"),
        llm_max_output_tokens=1000,
    )
    pricing = card()
    with BudgetLedger(tmp_path / "ledger.db", pricing, BudgetPolicy()) as ledger:
        report = run_population(
            tmp_path / "population",
            source=source,
            cases=(case, other),
            oracles=(oracle, other_oracle),
            policy=policy,
            freeze_hash="control-failure",
            frozen_revision="control",
            settings=settings,
            ledger=ledger,
            transport_factory=lambda: httpx.MockTransport(respond),
        )
        assert report.qualified_deliveries == 0
        assert report.numeric_total == 30 and report.fact_total == 24
        assert report.incremental_usd == Decimal("0.001020") * len(calls)
        assert all(a.status == "reconciled" for a in report.spending.attempts)
        assert report.report_usd == report.incremental_usd
        assert read_report(tmp_path / "population/report.json") == report
        raw = json.loads((tmp_path / "population/report.json").read_text())
        raw["spending"]["attempts"][0]["usd_cost"] = "0"
        (tmp_path / "tampered.json").write_text(json.dumps(raw))
        import pytest

        with pytest.raises(ValueError):
            read_report(tmp_path / "tampered.json")


def test_control_suite_interruption_is_retained_without_another_dispatch(tmp_path: Path) -> None:
    import httpx
    import pytest
    from pydantic import AnyHttpUrl, SecretStr

    from copilot.config import Settings
    from evaluation.supplier_quality_pilot.budget import BudgetLedger, BudgetPolicy
    from evaluation.supplier_quality_validation.suite import ValidationReport, run_population
    from tests.integration.evaluation.test_pilot_budget import card

    source, case, oracle, policy = control()
    calls: list[httpx.Request] = []

    def crash(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise KeyboardInterrupt("control process death")

    settings = Settings(
        database_url="sqlite:///:memory:",
        llm_provider="deepseek",
        llm_model="test-model",
        llm_base_url=AnyHttpUrl("https://provider.test"),
        llm_api_key=SecretStr("control-secret"),
        llm_max_output_tokens=1000,
    )

    def execute(ledger: BudgetLedger) -> ValidationReport:
        return run_population(
            tmp_path / "population",
            source=source,
            cases=(case,),
            oracles=(oracle,),
            policy=policy,
            freeze_hash="control-interruption",
            frozen_revision="control",
            settings=settings,
            ledger=ledger,
            transport_factory=lambda: httpx.MockTransport(crash),
        )

    pricing = card()
    with (
        BudgetLedger(tmp_path / "ledger.db", pricing, BudgetPolicy()) as ledger,
        pytest.raises(KeyboardInterrupt),
    ):
        execute(ledger)
    with BudgetLedger(tmp_path / "ledger.db", pricing, BudgetPolicy()) as ledger:
        report = execute(ledger)
        assert len(calls) == 1
        assert report.partial
        assert report.runs[0].case_status == "interrupted"
        assert report.runs[0].usd_cost is None
        assert report.numeric_total == 15 and report.fact_total == 12
        assert report.spending.unresolved_eur == Decimal("1.002000")


def test_last_case_process_death_does_not_return_stale_previous_report(tmp_path: Path) -> None:
    import json

    import httpx
    import pytest
    from pydantic import AnyHttpUrl, BaseModel, SecretStr

    from copilot.config import Settings
    from copilot.contracts import ProposedPlan
    from copilot.llm.offline_mock import OfflineMockLLM
    from copilot.llm.schemas import TaskUnderstandingOutput
    from copilot.services.llm import LLMCallContext, LLMGenerationOptions, LLMMessage
    from evaluation.supplier_quality_pilot.budget import BudgetLedger, BudgetPolicy
    from evaluation.supplier_quality_validation.suite import ValidationReport, run_population
    from tests.integration.evaluation.test_pilot_budget import card

    source, case, oracle, policy = control()
    other = case.model_copy(update={"case_id": "P04"})
    other_oracle = oracle.model_copy(update={"case_id": "P04"})
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise KeyboardInterrupt("last case died")
        payload = json.loads(request.content)
        messages = [LLMMessage.model_validate(m) for m in payload["messages"]]
        schema: type[BaseModel] = (
            TaskUnderstandingOutput
            if "output_schema" in json.loads(messages[-1].content)
            else ProposedPlan
        )
        parsed = (
            OfflineMockLLM()
            .generate_structured(
                messages=messages,
                output_schema=schema,
                context=LLMCallContext(
                    task_id="control",
                    trace_id="control",
                    node_name="control",
                    prompt_version="control",
                    schema_version="control",
                ),
                options=LLMGenerationOptions(max_output_tokens=1000),
            )
            .parsed_output
        )
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [
                    {"message": {"content": parsed.model_dump_json()}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 1000,
                    "prompt_cache_hit_tokens": 200,
                    "prompt_cache_miss_tokens": 800,
                    "completion_tokens": 100,
                    "total_tokens": 1100,
                },
            },
        )

    settings = Settings(
        database_url="sqlite:///:memory:",
        llm_provider="deepseek",
        llm_model="test-model",
        llm_base_url=AnyHttpUrl("https://provider.test"),
        llm_api_key=SecretStr("control-secret"),
        llm_max_output_tokens=1000,
    )

    def execute(ledger: BudgetLedger) -> ValidationReport:
        return run_population(
            tmp_path / "population",
            source=source,
            cases=(case, other),
            oracles=(oracle, other_oracle),
            policy=policy,
            freeze_hash="control-last-case",
            frozen_revision="control",
            settings=settings,
            ledger=ledger,
            transport_factory=lambda: httpx.MockTransport(respond),
        )

    pricing = card()
    with (
        BudgetLedger(tmp_path / "ledger.db", pricing, BudgetPolicy()) as ledger,
        pytest.raises(KeyboardInterrupt),
    ):
        execute(ledger)
    with BudgetLedger(tmp_path / "ledger.db", pricing, BudgetPolicy()) as ledger:
        report = execute(ledger)
        assert calls == 3
        assert report.qualified_deliveries == 1
        assert report.runs[1].case_status == "interrupted"
        assert report.runs[1].usd_cost is None
        assert len(report.spending.attempts) == 3
        assert report.partial and report.numeric_total == 30


def test_pdf_control_checks_visible_numbers_and_literal_oracle(tmp_path: Path) -> None:
    source, case, oracle, policy = control()
    case = case.model_copy(
        update={"output_format": "pdf", "request": case.request.replace("JSON", "PDF")}
    )
    run = run_validation_case(tmp_path, source=source, case=case, oracle=oracle, policy=policy)
    assert run.qualified_delivery, [a for a in run.assertions if not a.passed]
    assert run.numeric_total == 15 and run.numeric_passed == 15
    assert any(a.assertion_id.startswith("pdf_visible/") for a in run.assertions)
    assert all(a.passed for a in run.assertions if a.category == "artifact")


def test_missing_failing_assertion_cannot_turn_a_case_into_qualified(tmp_path: Path) -> None:
    import pytest

    source, case, oracle, policy = control()
    wrong = oracle.model_copy(
        update={
            "metrics": (
                oracle.metrics[0].model_copy(update={"value": Decimal(987654321)}),
                *oracle.metrics[1:],
            )
        }
    )
    run = run_validation_case(tmp_path, source=source, case=case, oracle=wrong, policy=policy)
    raw = run.model_dump(mode="json", exclude_computed_fields=True)
    raw["assertions"] = [a.model_dump(mode="json") for a in run.assertions if a.passed]
    from evaluation.supplier_quality_validation.runner import ValidationRun

    with pytest.raises(ValueError, match="assertion"):
        ValidationRun.model_validate(raw)


def _valid_control_response(request: "httpx.Request") -> "httpx.Response":
    import json

    import httpx
    from pydantic import BaseModel

    from copilot.contracts import ProposedPlan
    from copilot.llm.offline_mock import OfflineMockLLM
    from copilot.llm.schemas import TaskUnderstandingOutput
    from copilot.services.llm import LLMCallContext, LLMGenerationOptions, LLMMessage

    messages = [LLMMessage.model_validate(m) for m in json.loads(request.content)["messages"]]
    schema: type[BaseModel] = (
        TaskUnderstandingOutput
        if "output_schema" in json.loads(messages[-1].content)
        else ProposedPlan
    )
    result = OfflineMockLLM().generate_structured(
        messages=messages,
        output_schema=schema,
        context=LLMCallContext(
            task_id="control",
            trace_id="control",
            node_name="control",
            prompt_version="control",
            schema_version="control",
        ),
        options=LLMGenerationOptions(max_output_tokens=1000),
    )
    return httpx.Response(
        200,
        json={
            "model": "test-model",
            "choices": [
                {
                    "message": {"content": result.parsed_output.model_dump_json()},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1000,
                "prompt_cache_hit_tokens": 200,
                "prompt_cache_miss_tokens": 800,
                "completion_tokens": 100,
                "total_tokens": 1100,
            },
        },
    )


def test_waiting_control_is_partial_and_capture_failure_stops_next_case(
    tmp_path: Path,
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    import httpx
    from pydantic import AnyHttpUrl, SecretStr

    from copilot.config import Settings
    from evaluation.supplier_quality_pilot.budget import BudgetLedger, BudgetPolicy
    from evaluation.supplier_quality_validation.suite import run_population
    from tests.integration.evaluation.test_pilot_budget import card

    source, case, oracle, policy = control()
    settings = Settings(
        database_url="sqlite:///:memory:",
        llm_provider="deepseek",
        llm_model="test-model",
        llm_base_url=AnyHttpUrl("https://provider.test"),
        llm_api_key=SecretStr("control-secret"),
        llm_max_output_tokens=1000,
    )
    pending = case.model_copy(
        update={
            "request": "Analyze supplier quality for S-100 and generate a JSON report.",
            "scripted_answer": "Q2 2026",
        }
    )
    pricing = card()
    with BudgetLedger(tmp_path / "waiting.db", pricing, BudgetPolicy()) as ledger:
        report = run_population(
            tmp_path / "waiting",
            source=source,
            cases=(pending,),
            oracles=(oracle,),
            policy=policy,
            freeze_hash="control-waiting",
            frozen_revision="control",
            settings=settings,
            ledger=ledger,
            transport_factory=lambda: httpx.MockTransport(_valid_control_response),
        )
        assert report.runs[0].task_status == "WAITING_CLARIFICATION"
        assert report.partial
        assert report.numeric_total == 15 and report.qualified_deliveries == 0

    original = Path.write_text

    def fail_capture(
        path: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        if path.name == "evidence.json":
            raise IsADirectoryError("control capture failure")
        return original(path, data, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "write_text", fail_capture)
    other = case.model_copy(update={"case_id": "P04"})
    other_oracle = oracle.model_copy(update={"case_id": "P04"})
    with BudgetLedger(tmp_path / "capture.db", pricing, BudgetPolicy()) as ledger:
        report = run_population(
            tmp_path / "capture",
            source=source,
            cases=(case, other),
            oracles=(oracle, other_oracle),
            policy=policy,
            freeze_hash="control-capture",
            frozen_revision="control",
            settings=settings,
            ledger=ledger,
            transport_factory=lambda: httpx.MockTransport(_valid_control_response),
        )
        assert report.partial and report.qualified_deliveries == 0
        assert report.stop_reason == "HARNESS_IsADirectoryError"
        assert report.runs[0].task_status == "COMPLETED"
        assert report.runs[0].case_status == "interrupted"
        assert report.runs[1].case_status == "unrun"
        assert len(report.spending.attempts) == 2
        assert report.report_usd == Decimal("0.002040")
