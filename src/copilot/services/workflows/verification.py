"""Deterministic final verification for evidence and JSON report integrity."""

from __future__ import annotations

import hashlib
import json

from copilot.contracts import ArtifactType, EvidenceType, StepResultStatus
from copilot.services.workflows.errors import VerificationError
from copilot.services.workflows.models import WorkflowExecutionContext
from copilot.services.workflows.ports import ArtifactStore


class WorkflowVerifier:
    """Verify required results, evidence coverage, citations, and artifact bytes."""

    def __init__(self, artifact_store: ArtifactStore) -> None:
        self._artifact_store = artifact_store

    def verify(self, context: WorkflowExecutionContext) -> None:
        """Raise a safe non-repairable error when completion invariants are not met."""
        if len(context.step_results) != len(context.plan.steps) or any(
            result.status is not StepResultStatus.SUCCESS
            for result in context.step_results.values()
        ):
            raise VerificationError("Not every required step completed successfully")
        evidence_types = {item.source_type for item in context.evidence.values()}
        if not {
            EvidenceType.DOCUMENT,
            EvidenceType.DATABASE,
            EvidenceType.CALCULATION,
        }.issubset(evidence_types):
            raise VerificationError("Required evidence types are incomplete")
        if len(context.artifacts) != 1:
            raise VerificationError("Exactly one final report artifact is required")
        artifact = context.artifacts[0]
        path = self._artifact_store.path_for(artifact)
        content = path.read_bytes()
        if not content or len(content) != artifact.size_bytes:
            raise VerificationError("Artifact is missing, empty, or has an invalid size")
        checksum = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if checksum != artifact.checksum:
            raise VerificationError("Artifact checksum does not match committed metadata")
        if not set(context.evidence).issubset(artifact.evidence_ids):
            raise VerificationError("Artifact does not cite all workflow evidence")
        if artifact.type is ArtifactType.QUALITY_ANALYSIS_REPORT_JSON:
            self._verify_json(content, context)

    @staticmethod
    def _verify_json(content: bytes, context: WorkflowExecutionContext) -> None:
        try:
            report = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VerificationError("JSON report is not readable") from exc
        cited = report.get("evidence_references")
        if not isinstance(cited, list) or {item["evidence_id"] for item in cited} != set(
            context.evidence
        ):
            raise VerificationError("JSON report citations do not resolve to workflow evidence")
        analysis = report.get("analysis_results")
        if not isinstance(analysis, dict) or "metrics" not in analysis:
            raise VerificationError("JSON report is missing deterministic analysis results")
