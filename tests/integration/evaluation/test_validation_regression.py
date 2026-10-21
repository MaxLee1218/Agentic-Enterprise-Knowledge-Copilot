"""Independent paid-regression controls use old pilot facts and fake HTTP only."""

import hashlib
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import AnyHttpUrl, SecretStr

from copilot.config import Settings
from evaluation.dataset_loader import canonical_hash
from evaluation.supplier_quality_pilot.budget import BudgetLedger, BudgetPolicy, BudgetStopped
from evaluation.supplier_quality_validation.bundle import CaseOracle
from evaluation.supplier_quality_validation.runner import CaseInput
from evaluation.supplier_quality_validation.suite import ValidationReport, run_population
from tests.integration.evaluation.test_pilot_budget import card
from tests.integration.evaluation.test_validation_executor import control


def test_regression_keeps_baseline_fees_failures_and_does_not_repeat_cases(tmp_path: Path) -> None:
    from evaluation.supplier_quality_validation.regression import RegressionProtocol, run_regression

    source, case, oracle, policy = control()
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
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
    with BudgetLedger(tmp_path / "ledger.db", card(), BudgetPolicy()) as ledger:
        baseline = run_population(
            tmp_path / "first",
            source=source,
            cases=(case,),
            oracles=(oracle,),
            policy=policy,
            freeze_hash="control",
            frozen_revision="control",
            settings=settings,
            ledger=ledger,
            transport_factory=lambda: httpx.MockTransport(respond),
        )
        path = tmp_path / "first/report.json"
        original = path.read_bytes()
        protocol = RegressionProtocol(
            baseline_revision="control",
            baseline_report=str(path),
            baseline_sha256="sha256:" + hashlib.sha256(original).hexdigest(),
            baseline_canonical_hash=canonical_hash(baseline.model_dump(mode="json")),
            baseline_attempt_count=len(baseline.spending.attempts),
            baseline_attempt_records_sha256="control-only",
            freeze_hash="control",
            case_ids=(case.case_id,),
        )

        def execute(
            directory: Path,
            *,
            selected_cases: tuple[CaseInput, ...] = (case,),
            selected_oracles: tuple[CaseOracle, ...] = (oracle,),
            configured: Settings = settings,
            active_ledger: BudgetLedger = ledger,
        ) -> ValidationReport:
            return run_regression(
                directory,
                protocol=protocol,
                source=source,
                cases=selected_cases,
                oracles=selected_oracles,
                policy=policy,
                ledger=active_ledger,
                settings=configured,
                transport_factory=lambda: httpx.MockTransport(respond),
            )

        count = len(calls)
        result = execute(tmp_path / "regression")
        assert len(calls) == count * 2
        assert result.prior_attempt_ids == tuple(a.attempt_id for a in baseline.spending.attempts)
        assert result.spending.attempts[:count] == baseline.spending.attempts
        assert result.incremental_usd == Decimal("0.001020") * count
        assert result.report_total == 1 and result.numeric_total == 15 and result.fact_total == 12
        assert result.qualified_deliveries == 0 and result.cost_per_qualified_usd is None
        assert result.runs[0].task_status == "FAILED"
        assert path.read_bytes() == original
        from evaluation.supplier_quality_validation.audit import audit_pdf_reports
        from evaluation.supplier_quality_validation.comparison import compare_regression

        before_audit = audit_pdf_reports(baseline, (case,), audit_revision="control")
        after_audit = audit_pdf_reports(result, (case,), audit_revision="control")
        # Production uses the already-audited baseline when binding the new population.
        after_audit.config.parent_report_hash = canonical_hash(before_audit.model_dump(mode="json"))
        comparison = compare_regression(before_audit, after_audit)
        assert comparison.before.qualified_reports == comparison.after.qualified_reports == 0
        assert comparison.after.numeric_total == 15
        assert comparison.after.cost_per_qualified_usd is None
        assert comparison.cases[0].after_error == "LLM_SCHEMA_VALIDATION_ERROR"
        assert comparison.cases[0].before_latency_ms == baseline.runs[0].latency_ms
        assert comparison.cases[0].after_latency_ms == result.runs[0].latency_ms
        assert comparison.after.incremental_usd == Decimal("0.001020") * count
        with pytest.raises(ValueError, match="comparison identity changed"):
            compare_regression(before_audit, result)
        result.config.parent_report_hash = protocol.baseline_canonical_hash
        assert execute(tmp_path / "regression") == result
        assert len(calls) == count * 2
        with pytest.raises(BudgetStopped, match="VALIDATION_POPULATION_ALREADY_BOUND"):
            execute(tmp_path / "another")
        assert len(calls) == count * 2
        changes: tuple[Callable[[], ValidationReport], ...] = (
            lambda: execute(
                tmp_path / "changed",
                selected_cases=(case.model_copy(update={"request": case.request + " changed"}),),
            ),
            lambda: execute(
                tmp_path / "changed",
                selected_oracles=(oracle.model_copy(update={"numeric_ids": ()}),),
            ),
            lambda: execute(
                tmp_path / "changed",
                configured=settings.model_copy(update={"llm_model": "different-model"}),
            ),
        )
        for changed in changes:
            with pytest.raises(BudgetStopped):
                changed()
        with (
            BudgetLedger(tmp_path / "empty-ledger.db", ledger.pricing, ledger.policy) as empty,
            pytest.raises(BudgetStopped, match="REGRESSION_PRIOR_FEES_CHANGED"),
        ):
            execute(tmp_path / "changed", active_ledger=empty)
        assert len(calls) == count * 2
        path.write_bytes(original + b"\n")
        with pytest.raises(BudgetStopped, match="REGRESSION_BASELINE_BYTES_CHANGED"):
            execute(tmp_path / "changed-baseline")
        assert len(calls) == count * 2
        path.write_bytes(original)
