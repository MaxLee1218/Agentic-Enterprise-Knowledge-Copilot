"""Bounded intent parsing for interpretation confirmation, never governance approval."""

from __future__ import annotations

import re
from enum import StrEnum


class ConfirmationIntent(StrEnum):
    """Intent of a response to one displayed candidate interpretation."""

    AFFIRM = "AFFIRM"
    REJECT = "REJECT"
    CORRECT = "CORRECT"
    NONE = "NONE"


_AFFIRM = re.compile(
    r"^(?:yes(?:\s*,?\s*(?:continue|please))?|correct|that(?:'s| is) right|"
    r"go ahead(?: with that)?|sounds good)[.!\s]*$",
    re.IGNORECASE,
)
_REJECT = re.compile(r"^(?:no|nope|not that one)[.!\s]*$", re.IGNORECASE)
_CORRECTION = re.compile(
    r"^(?:no\b.+|actually\b.+|change\b.+|not\s+.+|instead\b.+)",
    re.IGNORECASE | re.DOTALL,
)


def parse_confirmation_intent(message: str | None) -> ConfirmationIntent:
    """Classify only explicit, bounded interpretation-confirmation phrases."""
    if message is None:
        return ConfirmationIntent.NONE
    normalized = " ".join(message.split())
    if _AFFIRM.fullmatch(normalized):
        return ConfirmationIntent.AFFIRM
    if _REJECT.fullmatch(normalized):
        return ConfirmationIntent.REJECT
    if _CORRECTION.match(normalized):
        return ConfirmationIntent.CORRECT
    return ConfirmationIntent.NONE


__all__ = ["ConfirmationIntent", "parse_confirmation_intent"]
