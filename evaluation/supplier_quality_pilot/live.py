"""Explicit live P01 composition using the existing provider and production Task Service."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import ValidationError

from copilot.config import PROJECT_ROOT, Settings
from copilot.llm.deepseek import DeepSeekProvider
from evaluation.supplier_quality_pilot.budget import (
    BudgetLedger,
    BudgetPolicy,
    BudgetStopped,
    GuardedTransport,
    PricingCard,
)
from evaluation.supplier_quality_pilot.contracts import PilotRun
from evaluation.supplier_quality_pilot.source import DEFAULT_SOURCE

DEFAULT_LEDGER = PROJECT_ROOT / "evaluation/reports/supplier-quality-pilot/budget.sqlite3"


@dataclass(frozen=True)
class LiveModelSession:
    """Explicit evaluation dependencies; credentials are never serialized into run metadata."""

    provider: DeepSeekProvider
    settings: Settings
    ledger: BudgetLedger
    run_id: str


def run_live_p01(
    output_directory: Path,
    *,
    pricing: PricingCard,
    policy: BudgetPolicy,
    source_path: Path = DEFAULT_SOURCE,
    settings: Settings | None = None,
    ledger_path: Path = DEFAULT_LEDGER,
    transport: httpx.BaseTransport | None = None,
) -> PilotRun:
    """Run explicit paid calibration; injected transports and ledger paths isolate offline tests.

    CLI always uses DEFAULT_LEDGER across runs and cases. No hidden transport retries, redirect
    following, or ambient proxy transports are allowed beneath the per-attempt spending guard.
    """
    from evaluation.supplier_quality_pilot.runner import run_p01

    try:
        configured = settings or Settings(database_url="sqlite:///:memory:")
    except ValidationError:
        raise BudgetStopped("LIVE_SETTINGS_INVALID") from None
    if configured.llm_provider != "deepseek":
        raise BudgetStopped("LIVE_PROVIDER_NOT_EXPLICIT")
    if configured.llm_api_key is None or not configured.llm_api_key.get_secret_value().strip():
        raise BudgetStopped("LIVE_CREDENTIAL_MISSING")
    if configured.llm_model != pricing.model:
        raise BudgetStopped("MODEL_PRICING_MISMATCH")
    endpoint = f"{str(configured.llm_base_url).rstrip('/')}/chat/completions"
    if endpoint != pricing.endpoint:
        raise BudgetStopped("ENDPOINT_PRICING_MISMATCH")
    if configured.llm_max_output_tokens > pricing.output_limit:
        raise BudgetStopped("OUTPUT_BOUND_MISMATCH")
    if not pricing.effective_at <= datetime.now(UTC) < pricing.expires_at:
        raise BudgetStopped("PRICING_EXPIRED_OR_NOT_EFFECTIVE")
    run_id = uuid4().hex
    with (
        BudgetLedger(ledger_path, pricing, policy) as ledger,
        httpx.Client(
            transport=GuardedTransport(
                ledger,
                transport
                if transport is not None
                else httpx.HTTPTransport(retries=0, trust_env=False),
                run_id=run_id,
            ),
            follow_redirects=False,
            timeout=httpx.Timeout(
                configured.llm_read_timeout_seconds, connect=configured.llm_connect_timeout_seconds
            ),
            headers={
                "Authorization": f"Bearer {configured.require_llm_api_key().get_secret_value()}",
                "User-Agent": configured.llm_user_agent,
            },
        ) as client,
    ):
        provider = DeepSeekProvider(
            api_key=configured.require_llm_api_key().get_secret_value(),
            model=configured.llm_model,
            base_url=str(configured.llm_base_url),
            client=client,
            max_retries=configured.llm_max_retries,
            retry_base_delay_seconds=configured.llm_retry_base_delay_seconds,
            trace_header=configured.llm_trace_header,
        )
        return run_p01(
            output_directory,
            source_path=source_path,
            live=LiveModelSession(provider, configured, ledger, run_id),
        )
