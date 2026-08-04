"""Central deterministic evaluation configuration and pricing."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic import Field

from evaluation.contracts import EvaluationModel


class PricingConfig(EvaluationModel):
    provider: str
    model: str
    currency: str = "USD"
    input_cost_per_million_tokens: Decimal = Field(ge=0)
    output_cost_per_million_tokens: Decimal = Field(ge=0)
    effective_from: str
    version: str


class RegressionConfig(EvaluationModel):
    tolerances: dict[str, Decimal] = Field(default_factory=dict)
    minimum_coverage: Decimal = Field(default=Decimal("1"), ge=0, le=1)
    hard_gate_metrics: tuple[str, ...] = (
        "overall_task_success_rate",
        "final_plan_validity",
        "citation_correctness",
        "numeric_accuracy",
        "safety_violation_rate",
    )


class EvaluationConfig(EvaluationModel):
    mode: str = "mock"
    seed: int = 42
    max_workers: int = Field(default=1, ge=1)
    timeout_seconds: int = Field(default=30, ge=1)
    output_dir: Path = Path("evaluation/reports")
    update_latest: bool = True
    pricing: PricingConfig | None = PricingConfig(
        provider="mock",
        model="offline-supplier-quality-eval-v1",
        input_cost_per_million_tokens=Decimal("0"),
        output_cost_per_million_tokens=Decimal("0"),
        effective_from="2026-08-03",
        version="mock-pricing.v1",
    )
    regression: RegressionConfig = Field(default_factory=RegressionConfig)


DEFAULT_DATASET = Path(__file__).resolve().parent / "datasets" / "supplier_quality_v1.jsonl"
DEFAULT_BASELINE = Path(__file__).resolve().parent / "baselines" / "supplier_quality_v1.json"


__all__ = ["DEFAULT_BASELINE", "DEFAULT_DATASET", "EvaluationConfig", "PricingConfig"]
