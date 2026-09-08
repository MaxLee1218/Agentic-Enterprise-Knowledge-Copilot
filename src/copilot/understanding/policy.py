"""Central acceptance policy for deterministic natural-language resolutions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from copilot.contracts import (
    CandidateInterpretation,
    FieldResolution,
    ResolutionStatus,
)


@dataclass(frozen=True, slots=True)
class ResolutionPolicy:
    """Classify accepted, confirmable, unresolved, and denied field outcomes."""

    def accepts(self, resolution: FieldResolution) -> bool:
        return resolution.status in {ResolutionStatus.EXACT, ResolutionStatus.NORMALIZED}

    def requires_confirmation(self, resolution: FieldResolution) -> bool:
        return resolution.status is ResolutionStatus.CONFIRMATION_REQUIRED

    def blocks_execution(self, resolution: FieldResolution) -> bool:
        return resolution.status not in {ResolutionStatus.EXACT, ResolutionStatus.NORMALIZED}

    def is_denial(self, resolution: FieldResolution) -> bool:
        return resolution.status is ResolutionStatus.UNAUTHORIZED


def build_candidate_interpretation(
    resolutions: tuple[FieldResolution, ...],
    display_text: str,
) -> CandidateInterpretation:
    """Create a stable hash over the exact displayed candidate set."""
    encoded = json.dumps(
        [item.model_dump(mode="json") for item in resolutions],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return CandidateInterpretation(
        version_hash=hashlib.sha256(encoded).hexdigest(),
        resolutions=resolutions,
        display_text=display_text,
    )


__all__ = ["ResolutionPolicy", "build_candidate_interpretation"]
