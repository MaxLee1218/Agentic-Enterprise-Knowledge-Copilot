"""Frozen cases run through the same production Task Service boundary as calibration."""

from pathlib import Path

import pytest

from evaluation.supplier_quality_pilot.cases import load_cases
from evaluation.supplier_quality_pilot.contracts import PilotAssertion
from evaluation.supplier_quality_pilot.runner import run_case


def test_single_supplier_q1_numbers_pass_but_wrong_scope_prose_fails(tmp_path: Path) -> None:
    case = load_cases()[3]
    run = run_case(tmp_path, case=case)
    assert not run.qualified_delivery
    assert not next(
        a for a in run.assertions if a.assertion_id == "scope_prose_matches_quarter"
    ).passed
    assert run.case_id == "P04"
    assert run.numeric_passed == run.numeric_total == 15


def test_empty_data_is_a_report_without_invented_zero_rates(tmp_path: Path) -> None:
    run = run_case(tmp_path, case=load_cases()[5])
    assert not run.qualified_delivery  # No records is correct, but the template says Q2 for Q3.
    assert run.numeric_passed == run.numeric_total == 1


def test_pdf_has_independent_numeric_checks_and_readable_pages(tmp_path: Path) -> None:
    run = run_case(tmp_path, case=load_cases()[1])
    assert run.qualified_delivery, [a for a in run.assertions if not a.passed]
    assert run.numeric_passed == run.numeric_total == 30


def test_policy_index_is_exact_and_unsupported_containment_claim_is_detected(
    tmp_path: Path,
) -> None:
    run = run_case(tmp_path, case=load_cases()[7])
    assertions = {a.assertion_id: a for a in run.assertions}
    assert assertions["support/policy_method_and_limit"].passed
    assert not assertions["policy_no_unsupported_actions"].passed
    assert not run.qualified_delivery
    assert run.numeric_passed == 30
    assert "knowledge_search" in run.executed_tools


def test_unauthorized_rejection_is_not_a_qualified_report(tmp_path: Path) -> None:
    run = run_case(tmp_path, case=load_cases()[9])
    assert not run.qualified_delivery
    assert all(a.passed for a in run.assertions), run.assertions
    assert not run.artifact_paths
    assert not run.executed_tools


def test_scripted_clarification_remains_pending_without_postgres(tmp_path: Path) -> None:
    run = run_case(tmp_path, case=load_cases()[8])
    assert not run.qualified_delivery
    assert run.error_code == "SCRIPTED_POSTGRES_RUNTIME_REQUIRED"
    assert run.interaction is not None
    assert run.interaction.initial_status == "WAITING_CLARIFICATION"
    assert run.interaction.tools_before_answer == 0


def test_scripted_clarification_resumes_through_postgres_worker(tmp_path: Path) -> None:
    import os

    import pytest

    url = os.environ.get("PILOT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("A dedicated migrated PILOT_TEST_POSTGRES_URL is required")
    run = run_case(tmp_path, case=load_cases()[8], persistence_url=url)
    assert run.error_code is None
    assert all(a.passed for a in run.assertions if a.category == "lifecycle")
    assert not run.qualified_delivery  # Existing mock pollutes scope from authorized context.
    assert run.numeric_passed == 15
    assert run.interaction is not None
    assert run.interaction.answer_applied
    assert run.interaction.initial_task_id == run.interaction.final_task_id == run.task_id
    assert run.interaction.initial_status == "WAITING_CLARIFICATION"
    assert run.interaction.tools_before_answer == 0


def test_offline_suite_keeps_fixed_denominators_and_pending_case(tmp_path: Path) -> None:
    from evaluation.supplier_quality_pilot.suite import run_suite

    report = run_suite(tmp_path)
    assert report.delivery_total == 9
    assert report.qualified_deliveries == 5
    assert report.numeric_total == 181
    assert report.safety_passed
    assert report.partial
    assert len(report.runs) == 10
    assert report.cost_per_qualified_usd is None
    assert (tmp_path / "suite.json").is_file()


@pytest.mark.parametrize("capture_failure", [False, True])
def test_suite_budget_stop_preserves_denominators_and_resume_never_calls_provider(
    tmp_path: Path,
    capture_failure: bool,
) -> None:
    import json
    from decimal import Decimal

    import httpx
    from pydantic import AnyHttpUrl, BaseModel, SecretStr

    from copilot.config import Settings
    from copilot.contracts import ProposedPlan
    from copilot.llm.offline_mock import OfflineMockLLM
    from copilot.llm.schemas import TaskUnderstandingOutput
    from copilot.services.llm import LLMCallContext, LLMGenerationOptions, LLMMessage
    from evaluation.supplier_quality_pilot.budget import BudgetPolicy
    from evaluation.supplier_quality_pilot.live import run_live_p01
    from evaluation.supplier_quality_pilot.suite import run_suite
    from tests.integration.evaluation.test_pilot_budget import card

    pricing = card().model_copy(
        update={"input_miss_per_million": Decimal("0.1"), "input_hit_per_million": Decimal("0.01")}
    )
    policy = BudgetPolicy(authorized_eur=Decimal("2.1029" if capture_failure else "2.1023"))
    settings = Settings(
        app_env="test",
        database_url="sqlite:///:memory:",
        llm_provider="deepseek",
        llm_api_key=SecretStr("fake"),
        llm_model="test-model",
        llm_base_url=AnyHttpUrl("https://provider.test"),
        llm_max_output_tokens=1000,
    )
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if capture_failure and len(calls) == 4:
            # Paid Task execution succeeds; capture then fails on a filesystem boundary.
            (tmp_path / "suite/P02-r0/model-inputs.json").mkdir()
        payload = json.loads(request.content)
        messages = [LLMMessage.model_validate(m) for m in payload["messages"]]
        schema: type[BaseModel] = (
            TaskUnderstandingOutput
            if "output_schema" in json.loads(messages[-1].content)
            else ProposedPlan
        )
        result = OfflineMockLLM().generate_structured(
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

    ledger = tmp_path / "ledger.db"
    calibration = tmp_path / "calibration"
    assert run_live_p01(
        calibration,
        pricing=pricing,
        policy=policy,
        settings=settings,
        ledger_path=ledger,
        transport=httpx.MockTransport(respond),
    ).qualified_delivery
    assert len(calls) == 2
    for resume in (False, True):
        report = run_suite(
            tmp_path / "suite",
            mode="live",
            pricing=pricing,
            policy=policy,
            settings=settings,
            calibration_directory=calibration,
            ledger_path=ledger,
            resume=resume,
            transport_factory=lambda: httpx.MockTransport(respond),
        )
        assert report.qualified_deliveries == 1
        assert report.numeric_total == 181
        assert report.stop_reason == "TOTAL_BUDGET"
        assert report.partial
        assert len(report.runs) == 10
        assert len(calls) == (4 if capture_failure else 2)
        assert report.spending is not None
        expected_cost = Decimal("0.001128" if capture_failure else "0.000564")
        assert report.spending.incurred_eur_upper == expected_cost
        assert report.first_delivery_cost_usd == expected_cost
        assert report.cost_per_qualified_usd == expected_cost
        if capture_failure:
            failed = next(r for r in report.runs if r.case_id == "P02")
            assert failed.case_status == "executed"
            assert failed.error_code == "HARNESS_IsADirectoryError"
            assert failed.model_cost == Decimal("0.000564")
            assert failed.latency_ms > 0
            assert failed.task_id is not None
            assert failed.task_status == "COMPLETED"


def test_pdf_visible_values_must_match_its_embedded_model(tmp_path: Path) -> None:
    import hashlib
    from dataclasses import replace

    from pydantic import TypeAdapter

    from copilot.services.workflows.models import WorkflowExecution
    from evaluation.supplier_quality_pilot.checker import evaluate_case
    from evaluation.supplier_quality_pilot.contracts import PilotSnapshot
    from evaluation.supplier_quality_pilot.source import DEFAULT_SOURCE

    case = load_cases()[1]
    full = run_case(tmp_path / "full", case=case)
    single = run_case(
        tmp_path / "single",
        case=case.model_copy(
            update={
                "suppliers": ("S-100",),
                "request": case.request.replace("S-100 and S-200", "S-100"),
            }
        ),
    )
    full_path = Path(full.artifact_paths[0])
    prefix = Path(single.artifact_paths[0]).read_bytes().split(b"%COPILOT_REPORT_MODEL:")[0]
    model = full_path.read_bytes().split(b"%COPILOT_REPORT_MODEL:")[1]
    full_path.write_bytes(prefix + b"%COPILOT_REPORT_MODEL:" + model)
    execution = TypeAdapter(WorkflowExecution).validate_json(
        (tmp_path / "full/execution.json").read_bytes()
    )
    artifact = execution.artifacts[0].model_copy(
        update={
            "size_bytes": full_path.stat().st_size,
            "checksum": "sha256:" + hashlib.sha256(full_path.read_bytes()).hexdigest(),
        }
    )
    execution = replace(execution, artifacts=(artifact,))
    checks = evaluate_case(
        PilotSnapshot.model_validate_json(DEFAULT_SOURCE.read_bytes()),
        execution,
        {artifact.artifact_id: full_path},
        case,
    )
    assert all(a.passed for a in checks if a.category == "numeric")
    assert any(not a.passed for a in checks if a.assertion_id.startswith("pdf_visible/"))


def test_empty_summary_cannot_claim_zero_defects_or_good_quality(tmp_path: Path) -> None:
    checks = _mutated_report_checks(tmp_path, 5, "empty")
    assert not next(a for a in checks if a.assertion_id == "empty_conclusions_are_bounded").passed


def test_policy_citations_must_identify_the_frozen_document_evidence(tmp_path: Path) -> None:
    checks = _mutated_report_checks(tmp_path, 7, "policy")
    assert not next(a for a in checks if a.assertion_id == "support/policy_method_and_limit").passed


def _mutated_report_checks(
    tmp_path: Path, case_index: int, mutation: str
) -> tuple["PilotAssertion", ...]:
    import hashlib
    import json
    from dataclasses import replace

    from pydantic import TypeAdapter

    from copilot.services.workflows.models import WorkflowExecution
    from evaluation.supplier_quality_pilot.checker import evaluate_case
    from evaluation.supplier_quality_pilot.contracts import PilotSnapshot
    from evaluation.supplier_quality_pilot.source import DEFAULT_SOURCE

    case = load_cases()[case_index]
    run = run_case(tmp_path, case=case)
    path = Path(run.artifact_paths[0])
    document = json.loads(path.read_bytes())
    if mutation == "empty":
        document["executive_summary"] = "All suppliers have a 0% defect rate. Quality is excellent."
    else:
        for field in ("applicable_policies", "quality_policy_findings"):
            for ref in document[field]:
                ref.update(
                    evidence_id="E-NONEXISTENT",
                    document_id="wrong-document",
                    document_version="wrong-version",
                    location="wrong-chunk",
                )
    path.write_text(json.dumps(document))
    execution = TypeAdapter(WorkflowExecution).validate_json(
        (tmp_path / "execution.json").read_bytes()
    )
    artifact = execution.artifacts[0].model_copy(
        update={
            "size_bytes": path.stat().st_size,
            "checksum": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    )
    return evaluate_case(
        PilotSnapshot.model_validate_json(DEFAULT_SOURCE.read_bytes()),
        replace(execution, artifacts=(artifact,)),
        {artifact.artifact_id: path},
        case,
    )
