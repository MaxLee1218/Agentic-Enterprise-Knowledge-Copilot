"""Deterministic natural-language field resolution for governed Task Understanding."""

from copilot.understanding.confirmation import ConfirmationIntent, parse_confirmation_intent
from copilot.understanding.date_normalizer import (
    normalize_accounts_payable_time_range,
    normalize_supplier_period,
)
from copilot.understanding.entity_resolver import (
    resolve_legal_entities,
    resolve_suppliers,
)
from copilot.understanding.policy import ResolutionPolicy, build_candidate_interpretation

__all__ = [
    "ConfirmationIntent",
    "ResolutionPolicy",
    "build_candidate_interpretation",
    "normalize_accounts_payable_time_range",
    "normalize_supplier_period",
    "parse_confirmation_intent",
    "resolve_legal_entities",
    "resolve_suppliers",
]
