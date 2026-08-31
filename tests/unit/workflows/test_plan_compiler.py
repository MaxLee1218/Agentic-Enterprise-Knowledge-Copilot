"""Deterministic PlanCompiler authority and compatibility coverage."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import JsonValue

from copilot.contracts import CapabilityName, JsonObject, ProposedPlan, ProposedStep
from copilot.services.domains import DomainCapabilityManifestRegistry
from copilot.services.domains.manifests import SUPPLIER_QUALITY_MANIFEST
from copilot.services.workflows.errors import (
    PlannerCompilationError,
    PlannerUnsupportedCapabilityError,
)
from copilot.services.workflows.plan_compiler import PlanCompiler
from copilot.services.workflows.validation import PlanValidator
from tests.unit.domain.ap_helpers import make_ap_contract
from tests.unit.domain.helpers import make_contract
from tests.workflow_helpers import build_test_container

COMPILED_AT = datetime(2026, 8, 30, 8, 0, tzinfo=UTC)


def _proposal(*, report_arguments: dict[str, JsonValue] | None = None) -> ProposedPlan:
    return ProposedPlan(
        steps=(
            ProposedStep(
                step_id="knowledge",
                capability=CapabilityName.KNOWLEDGE_SEARCH,
                purpose="Retrieve policy evidence",
            ),
            ProposedStep(
                step_id="database",
                capability=CapabilityName.DATABASE_QUERY,
                purpose="Retrieve governed business data",
            ),
            ProposedStep(
                step_id="analysis",
                capability=CapabilityName.ANALYSIS_ENGINE,
                purpose="Calculate deterministic findings",
                depends_on=("database",),
            ),
            ProposedStep(
                step_id="report",
                capability=CapabilityName.REPORT_GENERATOR,
                purpose="Generate the internal report",
                arguments=JsonObject(report_arguments or {}),
                depends_on=("knowledge", "analysis"),
            ),
        )
    )


@pytest.mark.parametrize(
    ("contract_factory", "expected_steps"),
    [(make_contract, 4), (make_ap_contract, 14)],
)
def test_compiler_resolves_exact_registry_metadata_and_valid_canonical_plan(
    tmp_path: Path,
    contract_factory: object,
    expected_steps: int,
) -> None:
    assert callable(contract_factory)
    contract = contract_factory()
    with build_test_container(tmp_path / "artifacts") as container:
        compiler = PlanCompiler(container.registry)

        result = compiler.compile(
            _proposal(),
            contract,
            planning_version=1,
            max_steps=14,
            created_at=COMPILED_AT,
        )
        validation = PlanValidator(
            registry=container.registry,
            max_task_steps=14,
        ).evaluate(result.plan, contract)

        assert validation.is_valid
        assert len(result.plan.steps) == expected_steps
        for step in result.plan.steps:
            definition = container.registry.get_profile(
                step.tool_name,
                step.tool_version,
                step.contract_profile,
            ).definition
            assert step.input_schema == definition.input_schema
            assert step.output_schema == definition.output_schema
            assert step.retry_policy.max_attempts in {2, 3}
            assert definition.timeout.attempt_seconds > 0
            assert definition.risk_level.value in {"LOW", "MEDIUM"}
            assert definition.approval_policy.policy_id
            assert definition.idempotency.key_components


def test_task_contract_arguments_override_planner_suggestions(tmp_path: Path) -> None:
    with build_test_container(tmp_path / "artifacts") as container:
        result = PlanCompiler(container.registry).compile(
            _proposal(report_arguments={"format": "JSON", "tenant_id_hint": "ignored"}),
            make_contract(),
            planning_version=1,
            max_steps=14,
            created_at=COMPILED_AT,
        )

    codes = {item.code for item in result.diagnostics}
    assert "TASK_CONTRACT_ARGUMENT_OVERRIDDEN" in codes
    assert all(not hasattr(step, "arguments") for step in result.plan.steps)


@pytest.mark.parametrize(
    ("contract_factory", "arguments"),
    (
        (
            make_contract,
            {
                "supplier_ids": ["UNAUTHORIZED-SUPPLIER"],
                "format": "JSON",
            },
        ),
        (
            make_ap_contract,
            {
                "legal_entity_ids": ["LE-UNAUTHORIZED"],
                "currency_scope": ["XXX"],
                "supplier_ids": ["SUP-UNAUTHORIZED"],
                "format": "PDF",
            },
        ),
    ),
)
def test_scope_and_deliverable_suggestions_never_change_canonical_execution(
    tmp_path: Path,
    contract_factory: object,
    arguments: dict[str, JsonValue],
) -> None:
    assert callable(contract_factory)
    contract = contract_factory()
    with build_test_container(tmp_path / "artifacts") as container:
        compiler = PlanCompiler(container.registry)
        baseline = compiler.compile(
            _proposal(),
            contract,
            planning_version=1,
            max_steps=14,
            created_at=COMPILED_AT,
        )
        attacked = compiler.compile(
            _proposal(report_arguments=arguments),
            contract,
            planning_version=1,
            max_steps=14,
            created_at=COMPILED_AT,
        )

    assert attacked.plan == baseline.plan
    assert any(item.code == "TASK_CONTRACT_ARGUMENT_OVERRIDDEN" for item in attacked.diagnostics)


def test_domain_dependencies_are_normalized_to_frozen_invariants(tmp_path: Path) -> None:
    proposal = _proposal()
    report = proposal.steps[-1].model_copy(update={"depends_on": ()})
    proposal = proposal.model_copy(update={"steps": (*proposal.steps[:-1], report)})
    with build_test_container(tmp_path / "artifacts") as container:
        result = PlanCompiler(container.registry).compile(
            proposal,
            make_contract(),
            planning_version=1,
            max_steps=14,
            created_at=COMPILED_AT,
        )

    assert any(item.code == "DOMAIN_DEPENDENCY_NORMALIZED" for item in result.diagnostics)
    assert set(result.plan.steps[-1].dependency) == {
        result.plan.steps[0].step_id,
        result.plan.steps[2].step_id,
    }


def test_compilation_is_deterministic_for_the_same_explicit_inputs(tmp_path: Path) -> None:
    with build_test_container(tmp_path / "artifacts") as container:
        compiler = PlanCompiler(container.registry)
        proposal = _proposal()
        contract = make_ap_contract()
        first = compiler.compile(
            proposal,
            contract,
            planning_version=2,
            max_steps=14,
            created_at=COMPILED_AT,
        )
        second = compiler.compile(
            proposal,
            contract,
            planning_version=2,
            max_steps=14,
            created_at=COMPILED_AT,
        )

        assert first == second


def test_missing_or_duplicate_capability_fails_closed(tmp_path: Path) -> None:
    missing = _proposal().model_copy(update={"steps": _proposal().steps[:-1]})
    duplicated = _proposal().model_copy(
        update={
            "steps": (
                *_proposal().steps,
                _proposal().steps[0].model_copy(update={"step_id": "k2"}),
            )
        }
    )
    with build_test_container(tmp_path / "artifacts") as container:
        compiler = PlanCompiler(container.registry)
        for proposal in (missing, duplicated):
            with pytest.raises(PlannerCompilationError):
                compiler.compile(
                    proposal,
                    make_contract(),
                    planning_version=1,
                    max_steps=14,
                    created_at=COMPILED_AT,
                )


def test_capability_outside_selected_domain_manifest_is_typed(tmp_path: Path) -> None:
    restricted_manifest = replace(
        SUPPLIER_QUALITY_MANIFEST,
        capability_profiles=SUPPLIER_QUALITY_MANIFEST.capability_profiles[:-1],
    )
    contract = make_contract().model_copy(
        update={"required_capabilities": restricted_manifest.capabilities}
    )
    manifests = DomainCapabilityManifestRegistry((restricted_manifest,))
    with build_test_container(tmp_path / "artifacts") as container:
        compiler = PlanCompiler(container.registry, domain_manifests=manifests)

        with pytest.raises(PlannerUnsupportedCapabilityError):
            compiler.compile(
                _proposal(),
                contract,
                planning_version=1,
                max_steps=14,
                created_at=COMPILED_AT,
            )


def test_contract_domain_profile_mismatch_fails_before_compilation(tmp_path: Path) -> None:
    mismatched = make_ap_contract().model_copy(update={"constraints": make_contract().constraints})
    with (
        build_test_container(tmp_path / "artifacts") as container,
        pytest.raises(PlannerCompilationError),
    ):
        PlanCompiler(container.registry).compile(
            _proposal(),
            mismatched,
            planning_version=1,
            max_steps=14,
            created_at=COMPILED_AT,
        )
