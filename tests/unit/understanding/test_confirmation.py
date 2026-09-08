"""Natural-language interpretation confirmation intent coverage."""

import pytest

from copilot.understanding.confirmation import ConfirmationIntent, parse_confirmation_intent


@pytest.mark.parametrize(
    "text",
    (
        "yes",
        "yes, continue",
        "correct",
        "that's right",
        "go ahead",
        "go ahead with that",
        "sounds good",
    ),
)
def test_affirmation_phrases(text: str) -> None:
    assert parse_confirmation_intent(text) is ConfirmationIntent.AFFIRM


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("no", ConfirmationIntent.REJECT),
        ("not that one", ConfirmationIntent.REJECT),
        ("no, use LE-US-01", ConfirmationIntent.CORRECT),
        ("actually use August only", ConfirmationIntent.CORRECT),
        ("change the entity to US", ConfirmationIntent.CORRECT),
    ),
)
def test_rejection_and_correction_phrases(text: str, expected: ConfirmationIntent) -> None:
    assert parse_confirmation_intent(text) is expected
