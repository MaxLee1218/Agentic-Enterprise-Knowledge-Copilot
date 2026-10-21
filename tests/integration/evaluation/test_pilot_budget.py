"""Paid-attempt guard tested at the supported provider HTTP boundary, entirely offline."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import AnyHttpUrl, SecretStr

from evaluation.supplier_quality_pilot.budget import (
    BudgetLedger,
    BudgetPolicy,
    BudgetStopped,
    GuardedTransport,
    PricingCard,
)


def card() -> PricingCard:
    now = datetime.now(UTC)
    return PricingCard(
        version="test-price.v1",
        provider="deepseek",
        model="test-model",
        response_models=("test-model",),
        endpoint="https://provider.test/chat/completions",
        effective_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=1),
        source_url="https://provider.test/pricing",
        bounds_source_url="https://provider.test/api",
        input_context_limit=1000000,
        output_limit=1000,
        request_bytes_limit=100000,
        input_miss_per_million=Decimal("1"),
        input_hit_per_million=Decimal("0.1"),
        output_per_million=Decimal("2"),
        fx_source_url="https://provider.test/test-fx",
        fx_reference_date=(now - timedelta(days=1)).date(),
        fx_reference_usd_per_eur=Decimal("1"),
        eur_per_usd_upper=Decimal("1"),
        tax_multiplier_upper=Decimal("1"),
        conversion_note="Test-only USD/EUR parity, no tax.",
    )


def test_price_renewal_preserves_old_attempts_and_aggregate_budget(tmp_path: Path) -> None:
    old = card()
    new = old.model_copy(update={"version": "renewed.v2", "output_per_million": Decimal("3")})
    path = tmp_path / "renewal.db"
    request = httpx.Request("POST", old.endpoint, json={"model": old.model, "max_tokens": 1000})
    body = {
        "model": old.model,
        "usage": {
            "prompt_tokens": 1000,
            "prompt_cache_hit_tokens": 200,
            "prompt_cache_miss_tokens": 800,
            "completion_tokens": 100,
            "total_tokens": 1100,
        },
    }
    with BudgetLedger(path, old, BudgetPolicy()) as ledger:
        attempt = ledger.reserve(request, run_id="old", stage="suite")
        ledger.reconcile(attempt, httpx.Response(200, json=body))
        original = ledger.snapshot().attempts
    with pytest.raises(BudgetStopped, match="LEDGER_CONFIG_MISMATCH"):
        BudgetLedger(path, new, BudgetPolicy())
    with BudgetLedger(path, new, BudgetPolicy(), previous_pricing=old) as ledger:
        assert ledger.snapshot().attempts == original
        assert len(ledger.snapshot().pricing_history) == 2
        attempt = ledger.reserve(request, run_id="new", stage="suite")
        ledger.reconcile(attempt, httpx.Response(200, json=body))
        assert ledger.snapshot().incurred_eur_upper == Decimal("0.002140")
    with BudgetLedger(path, new, BudgetPolicy()) as ledger:
        assert ledger.snapshot().attempts[0] == original[0]
        assert ledger.snapshot().attempts[1].usd_cost == Decimal("0.00112")
    with pytest.raises(BudgetStopped, match="LEDGER_CONFIG_MISMATCH"):
        BudgetLedger(path, new, BudgetPolicy(authorized_eur=Decimal("9")), previous_pricing=old)


def test_insufficient_calibration_budget_never_dispatches(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    with BudgetLedger(tmp_path / "ledger.db", card(), BudgetPolicy()) as ledger:
        transport = GuardedTransport(ledger, httpx.MockTransport(respond))
        with (
            httpx.Client(transport=transport) as client,
            pytest.raises(BudgetStopped, match="CALIBRATION_BUDGET"),
        ):
            client.post(card().endpoint, json={"model": "test-model", "max_tokens": 1000})
        assert calls == []
        assert ledger.snapshot().attempts == ()


def test_actual_cache_usage_reconciles_and_survives_restart(tmp_path: Path) -> None:
    pricing = card().model_copy(
        update={"input_miss_per_million": Decimal("0.1"), "input_hit_per_million": Decimal("0.01")}
    )
    body = {
        "model": "test-model",
        "usage": {
            "prompt_tokens": 1000,
            "prompt_cache_hit_tokens": 200,
            "prompt_cache_miss_tokens": 800,
            "completion_tokens": 100,
            "total_tokens": 1100,
        },
    }
    path = tmp_path / "ledger.db"
    with BudgetLedger(path, pricing, BudgetPolicy()) as ledger:
        with httpx.Client(
            transport=GuardedTransport(
                ledger, httpx.MockTransport(lambda r: httpx.Response(200, json=body))
            )
        ) as client:
            client.post(pricing.endpoint, json={"model": "test-model", "max_tokens": 1000})
        snapshot = ledger.snapshot()
        assert snapshot.incurred_eur_upper == Decimal("0.000282")
        assert snapshot.unresolved_eur == 0
        assert snapshot.attempts[0].usage == {
            "prompt_tokens": 1000,
            "prompt_cache_hit_tokens": 200,
            "prompt_cache_miss_tokens": 800,
            "completion_tokens": 100,
            "total_tokens": 1100,
            "reasoning_tokens": None,
        }
    with BudgetLedger(path, pricing, BudgetPolicy()) as resumed:
        assert resumed.snapshot().incurred_eur_upper == Decimal("0.000282")
        assert len(resumed.snapshot().attempts) == 1


def test_provider_automatic_retry_requires_another_reservation(tmp_path: Path) -> None:
    from pydantic import BaseModel

    from copilot.llm.deepseek import DeepSeekProvider
    from copilot.services.llm import LLMCallContext, LLMGenerationOptions, LLMMessage

    class Output(BaseModel):
        value: int

    pricing = card().model_copy(
        update={"input_miss_per_million": Decimal("0.1"), "input_hit_per_million": Decimal("0.01")}
    )
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            503,
            json={
                "model": "test-model",
                "usage": {
                    "prompt_tokens": 1000,
                    "prompt_cache_hit_tokens": 200,
                    "prompt_cache_miss_tokens": 800,
                    "completion_tokens": 100,
                    "total_tokens": 1100,
                },
            },
        )

    with BudgetLedger(
        tmp_path / "ledger.db", pricing, BudgetPolicy(calibration_eur=Decimal("0.1021"))
    ) as ledger:
        with httpx.Client(
            transport=GuardedTransport(ledger, httpx.MockTransport(respond))
        ) as client:
            provider = DeepSeekProvider(
                api_key="fake",
                model="test-model",
                base_url="https://provider.test",
                client=client,
                max_retries=3,
                retry_base_delay_seconds=0,
            )
            with pytest.raises(BudgetStopped, match="CALIBRATION_BUDGET"):
                provider.generate_structured(
                    messages=[LLMMessage(role="user", content="JSON")],
                    output_schema=Output,
                    context=LLMCallContext(
                        task_id="T-TEST",
                        trace_id="TRACE-TEST",
                        node_name="plan",
                        prompt_version="v1",
                        schema_version="v1",
                    ),
                    options=LLMGenerationOptions(max_output_tokens=1000),
                )
        assert len(calls) == 1
        assert ledger.snapshot().incurred_eur_upper == Decimal("0.000282")


@pytest.mark.parametrize("failure", ["missing", "timeout", "malformed", "negative", "over_limit"])
def test_uncertain_charge_stops_new_calls_and_restart(tmp_path: Path, failure: str) -> None:
    pricing = card().model_copy(
        update={"input_miss_per_million": Decimal("0.1"), "input_hit_per_million": Decimal("0.01")}
    )
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("possibly billed")
        if failure == "malformed":
            return httpx.Response(200, content=b"not json")
        usage = {
            "prompt_tokens": 1000,
            "prompt_cache_hit_tokens": 200,
            "prompt_cache_miss_tokens": 800,
            "completion_tokens": 100,
            "total_tokens": 1100,
        }
        if failure == "negative":
            usage["completion_tokens"] = -1
        if failure == "over_limit":
            usage.update(completion_tokens=1001, total_tokens=2001)
        return httpx.Response(
            200, json={"model": "test-model", **({} if failure == "missing" else {"usage": usage})}
        )

    path = tmp_path / "ledger.db"
    for _ in range(2):
        with BudgetLedger(path, pricing, BudgetPolicy()) as ledger:
            with (
                httpx.Client(
                    transport=GuardedTransport(ledger, httpx.MockTransport(respond))
                ) as client,
                pytest.raises(BudgetStopped),
            ):
                client.post(pricing.endpoint, json={"model": "test-model", "max_tokens": 1000})
            snapshot = ledger.snapshot()
            assert snapshot.unresolved_eur == Decimal("0.102000")
            assert snapshot.attempts[0].usd_cost is None
    assert len(calls) == 1


def test_live_task_boundary_preserves_failed_delivery_and_unknown_cost(tmp_path: Path) -> None:
    from copilot.config import Settings
    from evaluation.supplier_quality_pilot.live import run_live_p01

    pricing = card().model_copy(
        update={"input_miss_per_million": Decimal("0.1"), "input_hit_per_million": Decimal("0.01")}
    )
    calls = []
    settings = Settings(
        app_env="test",
        database_url="sqlite:///:memory:",
        llm_provider="deepseek",
        llm_model="test-model",
        llm_base_url=AnyHttpUrl("https://provider.test"),
        llm_api_key=SecretStr("fake-secret"),
        llm_max_output_tokens=1000,
    )

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    run = run_live_p01(
        tmp_path / "output",
        pricing=pricing,
        policy=BudgetPolicy(),
        settings=settings,
        ledger_path=tmp_path / "ledger.db",
        transport=httpx.MockTransport(respond),
    )
    assert len(calls) == 1
    assert run.mode == "live"
    assert run.provider == "deepseek"
    assert not run.qualified_delivery
    assert run.model_cost is None
    assert run.error_code == "USAGE_OR_BILLING_UNKNOWN"
    assert run.spending is not None
    assert run.spending.unresolved_eur == Decimal("0.102000")
    assert "fake-secret" not in (tmp_path / "output" / "report.json").read_text()


@pytest.mark.parametrize("calibration_limit", [Decimal("1"), Decimal("0.1025")])
def test_live_task_with_schema_repair_accounts_every_attempt(
    tmp_path: Path,
    calibration_limit: Decimal,
) -> None:
    import json

    from pydantic import BaseModel

    from copilot.config import Settings
    from copilot.contracts import ProposedPlan
    from copilot.llm.offline_mock import OfflineMockLLM
    from copilot.llm.schemas import TaskUnderstandingOutput
    from copilot.services.llm import LLMCallContext, LLMGenerationOptions, LLMMessage
    from evaluation.supplier_quality_pilot.live import run_live_p01

    pricing = card().model_copy(
        update={"input_miss_per_million": Decimal("0.1"), "input_hit_per_million": Decimal("0.01")}
    )
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
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
                    task_id="T-TEST",
                    trace_id="TRACE-TEST",
                    node_name="create_plan",
                    prompt_version="v1",
                    schema_version="v1",
                ),
                options=LLMGenerationOptions(max_output_tokens=1000),
            )
            .parsed_output
        )
        # A paid, schema-invalid planning response must still be charged before repair.
        content = "{}" if len(calls) == 2 else parsed.model_dump_json()
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
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
        app_env="test",
        database_url="sqlite:///:memory:",
        llm_provider="deepseek",
        llm_model="test-model",
        llm_base_url=AnyHttpUrl("https://provider.test"),
        llm_api_key=SecretStr("fake-secret"),
        llm_max_output_tokens=1000,
    )
    run = run_live_p01(
        tmp_path / "output",
        pricing=pricing,
        policy=BudgetPolicy(calibration_eur=calibration_limit),
        settings=settings,
        ledger_path=tmp_path / "ledger.db",
        transport=httpx.MockTransport(respond),
    )
    if calibration_limit < 1:
        assert not run.qualified_delivery
        assert run.error_code == "CALIBRATION_BUDGET"
        assert len(calls) == 2
        assert run.model_cost == Decimal("0.000564")
        return
    assert run.qualified_delivery, run.error_code
    assert len(calls) == 3
    assert run.spending is not None
    assert len(run.spending.attempts) == 3
    assert run.spending.incurred_eur_upper == Decimal("0.000846")
    assert run.model_cost == Decimal("0.000846")
    assert run.numeric_passed == 30


def test_abandoned_reservation_is_never_released_on_restart(tmp_path: Path) -> None:
    pricing = card().model_copy(
        update={"input_miss_per_million": Decimal("0.1"), "input_hit_per_million": Decimal("0.01")}
    )
    request = httpx.Request(
        "POST", pricing.endpoint, json={"model": "test-model", "max_tokens": 1000}
    )
    path = tmp_path / "ledger.db"
    with BudgetLedger(path, pricing, BudgetPolicy()) as ledger:
        ledger.reserve(request, run_id="crashed", stage="calibration")
        with pytest.raises(BudgetStopped, match="LEDGER_BUSY"):
            BudgetLedger(path, pricing, BudgetPolicy())
    with BudgetLedger(path, pricing, BudgetPolicy()) as resumed:
        with pytest.raises(BudgetStopped, match="UNRESOLVED_PRIOR_ATTEMPT"):
            resumed.reserve(request, run_id="different-case", stage="suite")
        assert resumed.snapshot().unresolved_eur == Decimal("0.102000")


@pytest.mark.parametrize("reason", ["expired", "model", "endpoint", "output", "total"])
def test_invalid_bounds_and_total_budget_block_before_dispatch(tmp_path: Path, reason: str) -> None:
    pricing = card().model_copy(
        update={"input_miss_per_million": Decimal("0.1"), "input_hit_per_million": Decimal("0.01")}
    )
    policy = BudgetPolicy(authorized_eur=Decimal("2.1")) if reason == "total" else BudgetPolicy()
    if reason == "expired":
        pricing = pricing.model_copy(
            update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    with BudgetLedger(tmp_path / "ledger.db", pricing, policy) as ledger:
        with (
            httpx.Client(
                transport=GuardedTransport(ledger, httpx.MockTransport(respond))
            ) as client,
            pytest.raises(BudgetStopped),
        ):
            client.post(
                pricing.endpoint + ("/wrong" if reason == "endpoint" else ""),
                json={
                    "model": "wrong" if reason == "model" else "test-model",
                    "max_tokens": 1001 if reason == "output" else 1000,
                },
            )
        assert calls == []


def test_unknown_ledger_schema_is_refused_without_resetting_records(tmp_path: Path) -> None:
    import sqlite3

    pricing = card()
    path = tmp_path / "ledger.db"
    with BudgetLedger(path, pricing, BudgetPolicy()):
        pass
    with sqlite3.connect(path) as database:
        database.execute("PRAGMA user_version=99")
    with pytest.raises(BudgetStopped, match="LEDGER_SCHEMA_UNSUPPORTED"):
        BudgetLedger(path, pricing, BudgetPolicy())
    with sqlite3.connect(path) as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 99
