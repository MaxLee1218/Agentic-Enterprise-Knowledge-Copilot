"""Post-fix populations use one persistent budget, without replacing first observations."""

import json
import os
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import AnyHttpUrl, BaseModel, SecretStr

from copilot.config import Settings
from copilot.contracts import ProposedPlan
from copilot.llm.offline_mock import OfflineMockLLM
from copilot.llm.schemas import ClarificationAssistantOutput, TaskUnderstandingOutput
from copilot.services.llm import LLMCallContext, LLMGenerationOptions, LLMMessage
from evaluation.supplier_quality_pilot.budget import BudgetPolicy, BudgetStopped
from evaluation.supplier_quality_pilot.comparison import publish_comparison
from evaluation.supplier_quality_pilot.live import run_live_p01
from evaluation.supplier_quality_pilot.suite import run_suite
from tests.integration.evaluation.test_pilot_budget import card


def test_regression_uses_the_original_ledger_and_cannot_dispatch_a_second_population(
    tmp_path: Path,
) -> None:
    url = os.environ.get("PILOT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Dedicated migrated PILOT_TEST_POSTGRES_URL required")
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = json.loads(request.content)
        messages = [LLMMessage.model_validate(m) for m in body["messages"]]
        data = json.loads(messages[-1].content)
        name = data.get("output_schema", {}).get("title")
        schema: type[BaseModel]
        if name == "TaskUnderstandingOutput":
            schema = TaskUnderstandingOutput
        elif name == "ClarificationAssistantOutput":
            schema = ClarificationAssistantOutput
        else:
            schema = ProposedPlan
        output = OfflineMockLLM().generate_structured(
            messages=messages,
            output_schema=schema,
            context=LLMCallContext(
                task_id="T-TEST",
                trace_id="TRACE-TEST",
                node_name="test",
                prompt_version="v1",
                schema_version="v1",
            ),
            options=LLMGenerationOptions(max_output_tokens=1000),
        )
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [
                    {
                        "message": {"content": output.parsed_output.model_dump_json()},
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

    pricing = card().model_copy(
        update={"input_miss_per_million": Decimal("0.1"), "input_hit_per_million": Decimal("0.01")}
    )
    policy = BudgetPolicy()
    settings = Settings(
        app_env="test",
        database_url="sqlite:///:memory:",
        llm_provider="deepseek",
        llm_api_key=SecretStr("fake"),
        llm_model="test-model",
        llm_base_url=AnyHttpUrl("https://provider.test"),
        llm_max_output_tokens=1000,
    )
    ledger, calibration = tmp_path / "budget.db", tmp_path / "calibration"
    assert run_live_p01(
        calibration,
        pricing=pricing,
        policy=policy,
        settings=settings,
        ledger_path=ledger,
        transport=httpx.MockTransport(respond),
    ).qualified_delivery
    # Spell out arguments to keep the public API boundary statically typed.
    before = run_suite(
        tmp_path / "first",
        calibration_directory=calibration,
        mode="live",
        pricing=pricing,
        policy=policy,
        settings=settings,
        ledger_path=ledger,
        persistence_url=url,
        transport_factory=lambda: httpx.MockTransport(respond),
    )
    assert before.qualified_deliveries == 9 and not before.partial
    baseline = tmp_path / "first/suite.json"
    baseline_bytes = baseline.read_bytes()
    initial_pointer = ledger.with_suffix(".suite.json").read_bytes()
    count = len(calls)
    after = run_suite(
        tmp_path / "post-fix",
        baseline_report_path=baseline,
        mode="live",
        pricing=pricing,
        policy=policy,
        settings=settings,
        ledger_path=ledger,
        persistence_url=url,
        transport_factory=lambda: httpx.MockTransport(respond),
    )
    assert after.qualified_deliveries == 9 and len(after.runs) == 10
    assert after.spending is not None and before.spending is not None
    assert len(after.spending.attempts) - len(before.spending.attempts) == len(calls) - count
    assert all(a.stage == "suite" for a in after.spending.attempts[len(before.spending.attempts) :])
    assert baseline.read_bytes() == baseline_bytes
    assert ledger.with_suffix(".suite.json").read_bytes() == initial_pointer
    comparison = publish_comparison(
        baseline, tmp_path / "post-fix/suite.json", tmp_path / "comparison"
    )
    assert comparison.incremental_regression_cost_usd == sum(
        (
            a.usd_cost
            for a in after.spending.attempts[len(before.spending.attempts) :]
            if a.usd_cost is not None
        ),
        Decimal(0),
    )
    count = len(calls)
    with pytest.raises(BudgetStopped, match="EXPERIMENT_SUITE_ALREADY_EXISTS"):
        run_suite(
            tmp_path / "another",
            baseline_report_path=baseline,
            mode="live",
            pricing=pricing,
            policy=policy,
            settings=settings,
            ledger_path=ledger,
            persistence_url=url,
            transport_factory=lambda: httpx.MockTransport(respond),
        )
    cached = run_suite(
        tmp_path / "post-fix",
        baseline_report_path=baseline,
        resume=True,
        mode="live",
        pricing=pricing,
        policy=policy,
        settings=settings,
        ledger_path=ledger,
        persistence_url=url,
        transport_factory=lambda: httpx.MockTransport(respond),
    )
    assert len(calls) == count
    assert cached.first_delivery_cost_usd == after.first_delivery_cost_usd
