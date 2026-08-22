"""Versioned deterministic normalization for AP business identifiers."""

from __future__ import annotations

import unicodedata

INVOICE_NUMBER_NORMALIZATION_VERSION = "invoice_number_normalization.v1"
_REMOVED_ASCII_CHARACTERS = str.maketrans("", "", " -/")


def normalize_invoice_number(value: str) -> str:
    """Normalize an invoice number using the frozen AP v1 algorithm."""
    if len(value) > 128:
        raise ValueError("invoice number must not exceed 128 characters")
    normalized = unicodedata.normalize("NFKC", value).strip().upper()
    normalized = normalized.translate(_REMOVED_ASCII_CHARACTERS)
    if not normalized:
        raise ValueError("invoice number normalization must not produce empty text")
    if len(normalized) > 128:
        raise ValueError("normalized invoice number must not exceed 128 characters")
    return normalized


__all__ = ["INVOICE_NUMBER_NORMALIZATION_VERSION", "normalize_invoice_number"]
