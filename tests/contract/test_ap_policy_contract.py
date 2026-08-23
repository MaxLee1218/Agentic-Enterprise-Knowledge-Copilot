"""Serialized contract compatibility for the frozen AP Stage 3 policy bundle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from copilot.contracts import APPolicyRuleManifestV1, ControlledPolicyCorpusManifestV1

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_ROOT = PROJECT_ROOT / "data" / "policies" / "accounts_payable" / "v1"


def test_committed_policy_contracts_round_trip_without_unknown_fields() -> None:
    corpus = ControlledPolicyCorpusManifestV1.model_validate_json(
        (BUNDLE_ROOT / "corpus-manifest.json").read_text(encoding="utf-8")
    )
    rules = APPolicyRuleManifestV1.model_validate_json(
        (BUNDLE_ROOT / "ap_rules.2026.1.json").read_text(encoding="utf-8")
    )

    assert ControlledPolicyCorpusManifestV1.model_validate_json(corpus.model_dump_json()) == corpus
    assert APPolicyRuleManifestV1.model_validate_json(rules.model_dump_json()) == rules


def test_policy_contract_rejects_unknown_fields_and_path_like_tenant() -> None:
    payload = json.loads((BUNDLE_ROOT / "corpus-manifest.json").read_text(encoding="utf-8"))
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        ControlledPolicyCorpusManifestV1.model_validate(payload)

    payload.pop("unexpected")
    payload["tenant_id"] = ".."
    payload["namespace"] = "tenant/../finance/accounts-payable/v1"
    with pytest.raises(ValidationError, match="safe identifier"):
        ControlledPolicyCorpusManifestV1.model_validate(payload)
