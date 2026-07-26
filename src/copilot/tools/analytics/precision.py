"""Central numeric precision rules for ``quality_metrics.v1``."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

DECIMAL_PLACES = 4
_QUANTUM = Decimal(1).scaleb(-DECIMAL_PLACES)


def normalize_decimal(value: Decimal | int | float) -> float:
    """Return one finite float quantized by the single v1 precision policy."""
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        normalized = decimal_value.quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("numeric value cannot be normalized") from exc
    if not normalized.is_finite():
        raise ValueError("numeric value must be finite")
    return float(normalized)


def normalized_ratio(numerator: int, denominator: int) -> float | None:
    """Calculate a bounded ratio without division-by-zero or binary-float drift."""
    if denominator == 0:
        return None
    return normalize_decimal(Decimal(numerator) / Decimal(denominator))


__all__ = ["DECIMAL_PLACES", "normalize_decimal", "normalized_ratio"]
