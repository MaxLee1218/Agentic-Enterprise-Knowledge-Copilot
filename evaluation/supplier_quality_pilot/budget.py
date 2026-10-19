"""Durable, single-concurrency evaluation spending authority at each HTTP attempt.

This ledger governs evaluation API spending only. It is never a Task or runtime lease.
"""

from __future__ import annotations

import fcntl
import json
import logging
import sqlite3
from datetime import UTC, date, datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import ConfigDict, Field, model_validator

from copilot.services.llm import LLMTokenBudgetExceededError
from evaluation.contracts import EvaluationModel
from evaluation.dataset_loader import canonical_hash

LOGGER = logging.getLogger(__name__)


class BudgetStopped(LLMTokenBudgetExceededError):
    """Non-retryable spending refusal with a safe reason, without provider payloads."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class PricingCard(EvaluationModel):
    """Explicit USD token rates and conservative EUR/tax conversion, not invoice costs."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["pilot-pricing.v1"] = "pilot-pricing.v1"
    version: str = Field(min_length=1)
    provider: Literal["deepseek"]
    model: str = Field(min_length=1)
    response_models: tuple[str, ...] = Field(min_length=1)
    endpoint: str = Field(pattern=r"^https://[^?#@]+/chat/completions$")
    effective_at: datetime
    expires_at: datetime
    source_url: str = Field(min_length=1)
    bounds_source_url: str = Field(min_length=1)
    currency: Literal["USD"] = "USD"
    input_context_limit: int = Field(gt=0)
    output_limit: int = Field(gt=0, le=65536)
    request_bytes_limit: int = Field(gt=0)
    input_miss_per_million: Decimal = Field(gt=0, allow_inf_nan=False)
    input_hit_per_million: Decimal = Field(gt=0, allow_inf_nan=False)
    output_per_million: Decimal = Field(gt=0, allow_inf_nan=False)
    reasoning_semantics: Literal["included_in_completion_tokens_and_max_tokens"] = (
        "included_in_completion_tokens_and_max_tokens"
    )
    reservation_method: Literal["entire_provider_context_at_cache_miss_rate"] = (
        "entire_provider_context_at_cache_miss_rate"
    )
    fx_source_url: str = Field(min_length=1)
    fx_reference_date: date
    fx_reference_usd_per_eur: Decimal = Field(gt=0, allow_inf_nan=False)
    eur_per_usd_upper: Decimal = Field(gt=0, allow_inf_nan=False)
    tax_multiplier_upper: Decimal = Field(ge=1, allow_inf_nan=False)
    conversion_note: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> PricingCard:
        if self.effective_at.utcoffset() is None or self.expires_at.utcoffset() is None:
            raise ValueError("pricing dates require UTC offsets")
        if self.expires_at <= self.effective_at:
            raise ValueError("pricing validity interval is empty")
        if self.input_hit_per_million > self.input_miss_per_million:
            raise ValueError("reservation requires cache miss to be the highest input rate")
        if self.eur_per_usd_upper < Decimal(1) / self.fx_reference_usd_per_eur:
            raise ValueError("EUR ceiling must cover the recorded reference exchange rate")
        if self.fx_reference_date > self.effective_at.date():
            raise ValueError("FX reference cannot follow price-card effective date")
        return self

    def eur_upper(self, usd: Decimal) -> Decimal:
        """Round each attempt upward to a micro-euro after conversion and tax contingency."""
        return (usd * self.eur_per_usd_upper * self.tax_multiplier_upper).quantize(
            Decimal("0.000001"), rounding=ROUND_CEILING
        )

    def maximum_eur(self, output_tokens: int) -> Decimal:
        """Reserve the entire published context window; never guess a local token count."""
        return self.eur_upper(
            (
                self.input_context_limit * self.input_miss_per_million
                + output_tokens * self.output_per_million
            )
            / Decimal(1000000)
        )


class BudgetPolicy(EvaluationModel):
    """Pilot authorization may be tightened, never silently expanded beyond €10/€1."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["pilot-budget.v1"] = "pilot-budget.v1"
    experiment_id: Literal["supplier-quality-value-pilot.v1"] = "supplier-quality-value-pilot.v1"
    authorized_eur: Decimal = Field(default=Decimal("10"), gt=0, le=10)
    reserve_eur: Decimal = Field(default=Decimal("2"), ge=2)
    calibration_eur: Decimal = Field(default=Decimal("1"), gt=0, le=1)

    @model_validator(mode="after")
    def validate_schedule(self) -> BudgetPolicy:
        if self.authorized_eur <= self.reserve_eur:
            raise ValueError("authorization must exceed the untouched reserve")
        return self


class PaidAttempt(EvaluationModel):
    """Durable reservation, including unsuccessful and possibly billed attempts."""

    schema_version: Literal["pilot-paid-attempt.v1"] = "pilot-paid-attempt.v1"
    attempt_id: str
    run_id: str
    stage: Literal["calibration", "suite"]
    reserved_at: datetime
    request_hash: str
    pricing_hash: str
    max_output_tokens: int
    reserved_eur: Decimal
    status: Literal["reserved", "reconciled", "uncertain"] = "reserved"
    usage: dict[str, int | None] | None = None
    usd_cost: Decimal | None = None
    eur_upper: Decimal | None = None
    reason: str | None = None


class SpendingSnapshot(EvaluationModel):
    """Machine-readable ledger export; unknown amounts remain reserved, never zero."""

    schema_version: Literal["pilot-spending.v1"] = "pilot-spending.v1"
    experiment_id: str
    pricing: PricingCard
    policy: BudgetPolicy
    stopped_reason: str | None
    attempts: tuple[PaidAttempt, ...]
    incurred_eur_upper: Decimal
    unresolved_eur: Decimal


class BudgetLedger:
    """One persistent experiment ledger, locked for the full synchronous run."""

    def __init__(self, path: Path, pricing: PricingCard, policy: BudgetPolicy) -> None:
        self.path, self.pricing, self.policy = path, pricing, policy
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = path.with_suffix(path.suffix + ".lock").open("a+b")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock.close()
            raise BudgetStopped("LEDGER_BUSY") from exc
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        self._db.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, record TEXT)")
        digest = canonical_hash(
            {"pricing": pricing.model_dump(mode="json"), "policy": policy.model_dump(mode="json")}
        )
        existing = self._get("config_hash")
        if existing is not None and existing != digest:
            self.close()
            raise BudgetStopped("LEDGER_CONFIG_MISMATCH")
        self._db.execute("INSERT OR IGNORE INTO metadata VALUES ('config_hash', ?)", (digest,))
        self._db.commit()
        # A process death leaves a reservation. No later process may assume it was unbilled.
        if any(a.status != "reconciled" for a in self.snapshot().attempts):
            self.stop("UNRESOLVED_PRIOR_ATTEMPT")

    def _get(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else None

    def stop(self, reason: str) -> None:
        """Persist the first refusal; all later calls and restarts inherit it."""
        self._db.execute("INSERT OR IGNORE INTO metadata VALUES ('stopped_reason', ?)", (reason,))
        self._db.commit()
        LOGGER.info("pilot_budget_stopped", extra={"reason": reason})

    def snapshot(self) -> SpendingSnapshot:
        """Export persisted attempt records without credentials, prompts or model responses."""
        attempts = tuple(
            PaidAttempt.model_validate_json(row[0])
            for row in self._db.execute("SELECT record FROM attempts ORDER BY rowid")
        )
        return SpendingSnapshot(
            experiment_id=self.policy.experiment_id,
            pricing=self.pricing,
            policy=self.policy,
            stopped_reason=self._get("stopped_reason"),
            attempts=attempts,
            incurred_eur_upper=sum(
                (a.eur_upper for a in attempts if a.eur_upper is not None), Decimal(0)
            ),
            unresolved_eur=sum(
                (a.reserved_eur for a in attempts if a.status != "reconciled"), Decimal(0)
            ),
        )

    def reserve(
        self, request: httpx.Request, *, run_id: str, stage: Literal["calibration", "suite"]
    ) -> PaidAttempt:
        """Durably reserve before dispatch, including every provider retry and repair call."""
        snapshot = self.snapshot()
        if snapshot.stopped_reason:
            raise BudgetStopped(snapshot.stopped_reason)
        now = datetime.now(UTC)
        reason = None
        if not self.pricing.effective_at <= now < self.pricing.expires_at:
            reason = "PRICING_EXPIRED_OR_NOT_EFFECTIVE"
        elif request.method != "POST" or str(request.url) != self.pricing.endpoint:
            reason = "ENDPOINT_MISMATCH"
        elif len(request.content) > self.pricing.request_bytes_limit:
            reason = "REQUEST_BYTES_LIMIT"
        try:
            payload = json.loads(request.content)
            output = payload["max_tokens"]
            valid = (
                payload["model"] == self.pricing.model
                and type(output) is int
                and 0 < output <= self.pricing.output_limit
                and not payload.get("stream")
                and not payload.get("tools")
                and payload.get("n", 1) == 1
            )
        except (ValueError, KeyError, TypeError):
            valid, output = False, 0
        if not valid:
            reason = reason or "UNBOUNDED_REQUEST"
        if reason:
            self.stop(reason)
            raise BudgetStopped(reason)
        maximum = self.pricing.maximum_eur(output)
        committed = snapshot.incurred_eur_upper + snapshot.unresolved_eur
        if committed + maximum > self.policy.authorized_eur - self.policy.reserve_eur:
            reason = "TOTAL_BUDGET"
        calibration = sum(
            (
                a.eur_upper
                if a.status == "reconciled" and a.eur_upper is not None
                else a.reserved_eur
                for a in snapshot.attempts
                if a.stage == "calibration"
            ),
            Decimal(0),
        )
        if stage == "calibration" and calibration + maximum > self.policy.calibration_eur:
            reason = reason or "CALIBRATION_BUDGET"
        if snapshot.unresolved_eur:
            reason = reason or "UNRESOLVED_PRIOR_ATTEMPT"
        if reason:
            self.stop(reason)
            raise BudgetStopped(reason)
        attempt = PaidAttempt(
            attempt_id=uuid4().hex,
            run_id=run_id,
            stage=stage,
            reserved_at=now,
            request_hash=canonical_hash(payload),
            pricing_hash=canonical_hash(self.pricing.model_dump(mode="json")),
            max_output_tokens=output,
            reserved_eur=maximum,
        )
        self._db.execute(
            "INSERT INTO attempts VALUES (?, ?)", (attempt.attempt_id, attempt.model_dump_json())
        )
        self._db.commit()
        LOGGER.info(
            "pilot_attempt_reserved",
            extra={"attempt_id": attempt.attempt_id, "reserved_eur": str(maximum)},
        )
        return attempt

    def _save_attempt(self, attempt: PaidAttempt) -> None:
        self._db.execute(
            "UPDATE attempts SET record=? WHERE id=?",
            (attempt.model_dump_json(), attempt.attempt_id),
        )
        self._db.commit()

    def uncertain(self, attempt: PaidAttempt, reason: str) -> None:
        """Retain the full reservation when billing cannot be reconciled."""
        attempt.status, attempt.reason = "uncertain", reason
        self._save_attempt(attempt)
        self.stop(reason)

    def reconcile(self, attempt: PaidAttempt, response: httpx.Response) -> None:
        """Use raw provider usage, including cache counts, before schema parsing can fail."""
        try:
            body = response.json()
            if body["model"] not in self.pricing.response_models:
                raise ValueError("model mismatch")
            raw = body["usage"]
            keys = (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "prompt_cache_hit_tokens",
                "prompt_cache_miss_tokens",
            )
            if any(type(raw[k]) is not int or raw[k] < 0 for k in keys):
                raise ValueError("invalid token counts")
            usage: dict[str, int | None] = {k: raw[k] for k in keys}
            reasoning = raw.get("completion_tokens_details", {}).get("reasoning_tokens")
            if reasoning is not None and (
                type(reasoning) is not int or not 0 <= reasoning <= raw["completion_tokens"]
            ):
                raise ValueError("invalid reasoning counts")
            usage["reasoning_tokens"] = reasoning
            if (
                raw["prompt_tokens"]
                != raw["prompt_cache_hit_tokens"] + raw["prompt_cache_miss_tokens"]
                or raw["total_tokens"] != raw["prompt_tokens"] + raw["completion_tokens"]
                or raw["prompt_tokens"] > self.pricing.input_context_limit
                or raw["completion_tokens"] > attempt.max_output_tokens
            ):
                raise ValueError("usage contradicts pricing bounds")
            usd = (
                raw["prompt_cache_hit_tokens"] * self.pricing.input_hit_per_million
                + raw["prompt_cache_miss_tokens"] * self.pricing.input_miss_per_million
                + raw["completion_tokens"] * self.pricing.output_per_million
            ) / Decimal(1000000)
            eur = self.pricing.eur_upper(usd)
            if eur > attempt.reserved_eur:
                raise ValueError("cost exceeds reservation")
        except (ValueError, KeyError, TypeError, AttributeError):
            self.uncertain(attempt, "USAGE_OR_BILLING_UNKNOWN")
            raise BudgetStopped("USAGE_OR_BILLING_UNKNOWN") from None
        attempt.status, attempt.usage = "reconciled", usage
        attempt.usd_cost, attempt.eur_upper = usd, eur
        self._save_attempt(attempt)
        LOGGER.info(
            "pilot_attempt_reconciled",
            extra={"attempt_id": attempt.attempt_id, "eur_upper": str(eur)},
        )

    def close(self) -> None:
        """Release experiment concurrency without releasing any spending reservations."""
        self._db.close()
        self._lock.close()

    def __enter__(self) -> BudgetLedger:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class GuardedTransport(httpx.BaseTransport):
    """Wrap a zero-retry HTTP transport so every actual network attempt is accounted for."""

    def __init__(
        self,
        ledger: BudgetLedger,
        delegate: httpx.BaseTransport,
        *,
        run_id: str = "P01",
        stage: Literal["calibration", "suite"] = "calibration",
    ) -> None:
        self.ledger, self.delegate, self.run_id, self.stage = ledger, delegate, run_id, stage

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Reserve durably immediately before the delegated network operation."""
        attempt = self.ledger.reserve(request, run_id=self.run_id, stage=self.stage)
        try:
            response = self.delegate.handle_request(request)
            response.read()
        except Exception:
            self.ledger.uncertain(attempt, "TRANSPORT_BILLING_UNKNOWN")
            raise BudgetStopped("TRANSPORT_BILLING_UNKNOWN") from None
        try:
            self.ledger.reconcile(attempt, response)
        except BudgetStopped:
            response.close()
            raise
        return response

    def close(self) -> None:
        """Close the wrapped transport; the ledger is owned by the evaluation runner."""
        self.delegate.close()
