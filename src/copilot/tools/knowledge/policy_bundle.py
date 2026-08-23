"""Validate and atomically publish the controlled Accounts Payable v1 policy bundle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from copilot.contracts import (
    APPolicyRuleKind,
    APPolicyRuleManifestV1,
    APPolicyRuleV1,
    APPolicySnapshotV1,
    ControlledPolicyCorpusManifestV1,
    ControlledPolicyDocumentV1,
)

CORPUS_MANIFEST_FILENAME = "corpus-manifest.json"
RULE_MANIFEST_FILENAME = "ap_rules.2026.1.json"
_CHUNK_START = re.compile(r"^<!-- policy-chunk:([A-Za-z0-9][A-Za-z0-9._-]{0,127}) -->$")
_CHUNK_END = "<!-- /policy-chunk -->"
_INDEX_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_UNTRUSTED_INSTRUCTION_PATTERNS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "reveal the system prompt",
    "bypass the policy",
    "execute arbitrary sql",
    "<script",
)


class APPolicyBundleError(RuntimeError):
    """Safe typed failure for policy validation or atomic publication."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class LoadedPolicyChunk:
    """Validated untrusted policy text paired with controlled metadata."""

    chunk_id: str
    page: int
    excerpt: str
    excerpt_checksum: str


@dataclass(frozen=True, slots=True)
class LoadedPolicyDocument:
    """One checksum-validated policy fixture."""

    descriptor: ControlledPolicyDocumentV1
    chunks: tuple[LoadedPolicyChunk, ...]


@dataclass(frozen=True, slots=True)
class LoadedAPPolicyBundle:
    """Validated AP policy corpus and deterministic executable rules."""

    root: Path
    corpus: ControlledPolicyCorpusManifestV1
    rule_manifest: APPolicyRuleManifestV1
    documents: tuple[LoadedPolicyDocument, ...]
    rag_payload: tuple[dict[str, Any], ...]
    payload_checksum: str

    def resolve_rule(
        self,
        kind: APPolicyRuleKind,
        *,
        effective_on: date,
        legal_entity_id: str | None = None,
    ) -> APPolicyRuleV1:
        """Resolve one exact applicable rule without latest-version or cross-tenant fallback."""
        candidates = tuple(
            rule
            for rule in self.rule_manifest.rules
            if rule.kind is kind
            and rule.effective_from <= effective_on <= rule.effective_to
            and (
                not rule.legal_entity_ids
                or (legal_entity_id is not None and legal_entity_id in rule.legal_entity_ids)
            )
        )
        if len(candidates) != 1:
            raise APPolicyBundleError(
                "POLICY_RULE_UNAVAILABLE",
                "Exactly one applicable controlled AP rule is required",
            )
        return candidates[0]


def load_ap_policy_bundle(
    root: Path,
    *,
    expected_tenant_id: str | None = None,
) -> LoadedAPPolicyBundle:
    """Load a bundle only after checksums, dates, namespace, and all bindings agree."""
    bundle_root = root.resolve()
    corpus_raw = _read_json_object(bundle_root / CORPUS_MANIFEST_FILENAME)
    rules_raw = _read_json_object(bundle_root / RULE_MANIFEST_FILENAME)
    try:
        corpus = ControlledPolicyCorpusManifestV1.model_validate(corpus_raw)
        rule_manifest = APPolicyRuleManifestV1.model_validate(rules_raw)
    except ValidationError as exc:
        raise APPolicyBundleError(
            "POLICY_RULE_MANIFEST_INVALID",
            "AP policy corpus or rule manifest violates its frozen schema",
        ) from exc

    if expected_tenant_id is not None and corpus.tenant_id != expected_tenant_id:
        raise APPolicyBundleError(
            "POLICY_TENANT_MISMATCH",
            "AP policy corpus does not belong to the expected tenant",
        )
    if rule_manifest.tenant_id != corpus.tenant_id:
        raise APPolicyBundleError(
            "POLICY_TENANT_MISMATCH",
            "AP policy corpus and rule manifest use different tenants",
        )
    if rule_manifest.policy_profile != corpus.policy_profile:
        raise APPolicyBundleError(
            "POLICY_PROFILE_MISMATCH",
            "AP policy corpus and rule manifest use different profiles",
        )
    _validate_declared_checksum(corpus_raw, "corpus_checksum", corpus.corpus_checksum)
    _validate_declared_checksum(
        rules_raw,
        "manifest_checksum",
        rule_manifest.manifest_checksum,
    )

    documents = tuple(_load_document(bundle_root, item) for item in corpus.documents)
    _validate_rule_bindings(rule_manifest, documents)
    payload = _build_rag_payload(corpus, rule_manifest, documents)
    return LoadedAPPolicyBundle(
        root=bundle_root,
        corpus=corpus,
        rule_manifest=rule_manifest,
        documents=documents,
        rag_payload=payload,
        payload_checksum=_checksum_bytes(_canonical_json_lines(payload)),
    )


def publish_ap_policy_bundle(
    bundle: LoadedAPPolicyBundle,
    output_root: Path,
    *,
    index_revision: str,
    published_at: datetime | None = None,
) -> APPolicySnapshotV1:
    """Publish one immutable snapshot and move the tenant pointer only after full validation."""
    clean_revision = index_revision.strip()
    if _INDEX_REVISION.fullmatch(clean_revision) is None:
        raise APPolicyBundleError(
            "POLICY_INDEX_REVISION_INVALID",
            "Policy index revision must be a bounded safe identifier",
        )
    publication_time = published_at or datetime.now(UTC)
    if publication_time.tzinfo is None or publication_time.utcoffset() != UTC.utcoffset(
        publication_time
    ):
        raise APPolicyBundleError(
            "POLICY_PUBLICATION_TIME_INVALID",
            "Policy publication time must be timezone-aware UTC",
        )

    identity = {
        "tenant_id": bundle.corpus.tenant_id,
        "namespace": bundle.corpus.namespace,
        "collection_id": bundle.corpus.collection_id,
        "rule_set_version": bundle.rule_manifest.rule_set_version,
        "manifest_checksum": bundle.rule_manifest.manifest_checksum,
        "corpus_checksum": bundle.corpus.corpus_checksum,
        "payload_checksum": bundle.payload_checksum,
        "index_revision": clean_revision,
    }
    snapshot_id = f"ap-policy-{_checksum_object(identity).removeprefix('sha256:')[:24]}"
    snapshot_without_checksum: dict[str, Any] = {
        "schema_version": "ap-policy-snapshot.v1",
        "snapshot_id": snapshot_id,
        "index_revision": clean_revision,
        "tenant_id": bundle.corpus.tenant_id,
        "namespace": bundle.corpus.namespace,
        "collection_id": bundle.corpus.collection_id,
        "policy_profile": bundle.corpus.policy_profile,
        "rule_set_id": bundle.rule_manifest.rule_set_id,
        "rule_set_version": bundle.rule_manifest.rule_set_version,
        "manifest_checksum": bundle.rule_manifest.manifest_checksum,
        "corpus_checksum": bundle.corpus.corpus_checksum,
        "payload_checksum": bundle.payload_checksum,
        "documents": [
            {
                "document_id": item.descriptor.document_id,
                "document_version": item.descriptor.document_version,
                "checksum": item.descriptor.checksum,
                "chunk_ids": [chunk.chunk_id for chunk in item.chunks],
            }
            for item in bundle.documents
        ],
        "binding_count": len(bundle.rule_manifest.rules),
        "published_at": publication_time.isoformat().replace("+00:00", "Z"),
    }
    snapshot = APPolicySnapshotV1.model_validate(
        {
            **snapshot_without_checksum,
            "publication_checksum": _checksum_object(snapshot_without_checksum),
        }
    )
    tenant_root = output_root.resolve() / bundle.corpus.tenant_id
    try:
        tenant_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise APPolicyBundleError(
            "POLICY_PUBLICATION_FAILED",
            "Policy snapshot publication root is unavailable",
        ) from exc
    final_directory = tenant_root / snapshot.snapshot_id
    if final_directory.exists():
        existing = _load_published_snapshot(final_directory / "snapshot.json")
        existing_identity = existing.model_dump(
            mode="json", exclude={"published_at", "publication_checksum"}
        )
        requested_identity = snapshot.model_dump(
            mode="json", exclude={"published_at", "publication_checksum"}
        )
        if existing_identity != requested_identity:
            raise APPolicyBundleError(
                "POLICY_SNAPSHOT_CONFLICT",
                "An immutable policy snapshot already exists with different metadata",
            )
        snapshot = existing
    else:
        staging: Path | None = None
        try:
            staging = Path(tempfile.mkdtemp(prefix=".policy-staging-", dir=tenant_root))
            _write_snapshot_files(staging, bundle, snapshot)
            os.replace(staging, final_directory)
        except OSError as exc:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            raise APPolicyBundleError(
                "POLICY_PUBLICATION_FAILED",
                "Policy snapshot could not be atomically published",
            ) from exc
    try:
        _replace_current_pointer(tenant_root, snapshot)
    except OSError as exc:
        raise APPolicyBundleError(
            "POLICY_PUBLICATION_FAILED",
            "Policy snapshot was retained but the current pointer was not advanced",
        ) from exc
    return snapshot


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise APPolicyBundleError(
            "POLICY_BUNDLE_UNAVAILABLE",
            f"Controlled policy file is unavailable or invalid: {path.name}",
        ) from exc
    if not isinstance(value, dict):
        raise APPolicyBundleError(
            "POLICY_RULE_MANIFEST_INVALID",
            f"Controlled policy file must contain one JSON object: {path.name}",
        )
    return value


def _validate_declared_checksum(
    value: dict[str, Any],
    checksum_field: str,
    expected: str,
) -> None:
    canonical = dict(value)
    canonical.pop(checksum_field, None)
    if _checksum_object(canonical) != expected:
        raise APPolicyBundleError(
            "POLICY_MANIFEST_CHECKSUM_MISMATCH",
            "Controlled policy manifest checksum does not match its content",
        )


def _load_document(
    root: Path,
    descriptor: ControlledPolicyDocumentV1,
) -> LoadedPolicyDocument:
    path = (root / descriptor.content_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise APPolicyBundleError(
            "POLICY_DOCUMENT_PATH_INVALID",
            "Controlled policy document escaped its bundle root",
        ) from exc
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise APPolicyBundleError(
            "POLICY_DOCUMENT_UNAVAILABLE",
            f"Controlled policy document is unavailable: {path.name}",
        ) from exc
    if _checksum_bytes(raw) != descriptor.checksum:
        raise APPolicyBundleError(
            "POLICY_DOCUMENT_CHECKSUM_MISMATCH",
            f"Controlled policy document checksum mismatch: {path.name}",
        )
    folded = text.casefold()
    if any(pattern in folded for pattern in _UNTRUSTED_INSTRUCTION_PATTERNS):
        raise APPolicyBundleError(
            "POLICY_DOCUMENT_UNSAFE_CONTENT",
            f"Controlled policy document failed the sanitized fixture gate: {path.name}",
        )
    extracted = _extract_chunks(text, path.name)
    expected = {chunk.chunk_id: chunk for chunk in descriptor.chunks}
    if set(extracted) != set(expected):
        raise APPolicyBundleError(
            "POLICY_DOCUMENT_CHUNK_MISMATCH",
            f"Controlled policy chunks do not match the manifest: {path.name}",
        )
    chunks: list[LoadedPolicyChunk] = []
    for chunk_id, chunk_descriptor in expected.items():
        excerpt = extracted[chunk_id]
        checksum = _checksum_bytes(excerpt.encode("utf-8"))
        if checksum != chunk_descriptor.excerpt_checksum:
            raise APPolicyBundleError(
                "POLICY_DOCUMENT_CHECKSUM_MISMATCH",
                f"Controlled policy excerpt checksum mismatch: {chunk_id}",
            )
        chunks.append(
            LoadedPolicyChunk(
                chunk_id=chunk_id,
                page=chunk_descriptor.page,
                excerpt=excerpt,
                excerpt_checksum=checksum,
            )
        )
    return LoadedPolicyDocument(descriptor=descriptor, chunks=tuple(chunks))


def _extract_chunks(text: str, filename: str) -> dict[str, str]:
    chunks: dict[str, str] = {}
    active: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        start = _CHUNK_START.fullmatch(line.strip())
        if start is not None:
            if active is not None or start.group(1) in chunks:
                raise APPolicyBundleError(
                    "POLICY_DOCUMENT_CHUNK_MISMATCH",
                    f"Controlled policy document has duplicate or nested chunks: {filename}",
                )
            active = start.group(1)
            lines = []
            continue
        if line.strip() == _CHUNK_END:
            if active is None:
                raise APPolicyBundleError(
                    "POLICY_DOCUMENT_CHUNK_MISMATCH",
                    f"Controlled policy document has an unmatched chunk marker: {filename}",
                )
            excerpt = "\n".join(lines).strip()
            if not excerpt:
                raise APPolicyBundleError(
                    "POLICY_DOCUMENT_CHUNK_MISMATCH",
                    f"Controlled policy document has an empty chunk: {filename}",
                )
            chunks[active] = excerpt
            active = None
            lines = []
            continue
        if active is not None:
            lines.append(line)
    if active is not None:
        raise APPolicyBundleError(
            "POLICY_DOCUMENT_CHUNK_MISMATCH",
            f"Controlled policy document has an unclosed chunk: {filename}",
        )
    return chunks


def _validate_rule_bindings(
    manifest: APPolicyRuleManifestV1,
    documents: tuple[LoadedPolicyDocument, ...],
) -> None:
    by_id = {item.descriptor.document_id: item for item in documents}
    for rule in manifest.rules:
        binding = rule.binding
        document = by_id.get(binding.document_id)
        if document is None:
            raise APPolicyBundleError(
                "POLICY_RULE_UNAVAILABLE",
                f"Rule binding document is unavailable: {rule.rule_id}",
            )
        descriptor = document.descriptor
        chunks = {chunk.chunk_id: chunk for chunk in document.chunks}
        chunk = chunks.get(binding.chunk_id)
        exact_binding = (
            descriptor.document_version == binding.document_version
            and descriptor.checksum == binding.document_checksum
            and chunk is not None
            and chunk.page == binding.page
            and chunk.excerpt_checksum == binding.excerpt_checksum
        )
        effective_binding = (
            descriptor.effective_from <= rule.effective_from
            and descriptor.effective_to >= rule.effective_to
        )
        if not exact_binding or not effective_binding:
            raise APPolicyBundleError(
                "POLICY_RULE_BINDING_MISMATCH",
                f"Rule binding does not resolve to one exact policy excerpt: {rule.rule_id}",
            )


def _build_rag_payload(
    corpus: ControlledPolicyCorpusManifestV1,
    manifest: APPolicyRuleManifestV1,
    documents: tuple[LoadedPolicyDocument, ...],
) -> tuple[dict[str, Any], ...]:
    rule_ids_by_chunk: dict[tuple[str, str], list[str]] = {}
    for rule in manifest.rules:
        key = (rule.binding.document_id, rule.binding.chunk_id)
        rule_ids_by_chunk.setdefault(key, []).append(rule.rule_id)
    payload: list[dict[str, Any]] = []
    for document in documents:
        descriptor = document.descriptor
        for chunk in document.chunks:
            payload.append(
                {
                    "content": chunk.excerpt,
                    "source": descriptor.document_id,
                    "chunk_id": chunk.chunk_id,
                    "metadata": {
                        "document_id": descriptor.document_id,
                        "document_version": descriptor.document_version,
                        "page": chunk.page,
                        "effective_from": descriptor.effective_from.isoformat(),
                        "effective_to": descriptor.effective_to.isoformat(),
                        "classification": descriptor.classification,
                        "owner": descriptor.owner,
                        "approved_by": descriptor.approved_by,
                        "approved_at": descriptor.approved_at.isoformat().replace("+00:00", "Z"),
                        "language": descriptor.language,
                        "policy_profile": corpus.policy_profile,
                        "policy_rule_set_version": manifest.rule_set_version,
                        "bound_rule_ids": sorted(
                            rule_ids_by_chunk.get((descriptor.document_id, chunk.chunk_id), ())
                        ),
                        "checksum": chunk.excerpt_checksum,
                        "document_checksum": descriptor.checksum,
                        "collection_id": corpus.collection_id,
                        "namespace": corpus.namespace,
                        "tenant_id": corpus.tenant_id,
                    },
                }
            )
    return tuple(payload)


def _write_snapshot_files(
    directory: Path,
    bundle: LoadedAPPolicyBundle,
    snapshot: APPolicySnapshotV1,
) -> None:
    (directory / "documents.jsonl").write_bytes(_canonical_json_lines(bundle.rag_payload))
    (directory / RULE_MANIFEST_FILENAME).write_text(
        bundle.rule_manifest.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (directory / "snapshot.json").write_text(
        snapshot.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def _replace_current_pointer(tenant_root: Path, snapshot: APPolicySnapshotV1) -> None:
    payload = {
        "schema_version": "ap-policy-current.v1",
        "snapshot_id": snapshot.snapshot_id,
        "publication_checksum": snapshot.publication_checksum,
    }
    descriptor, temporary_name = tempfile.mkstemp(prefix=".current-", dir=tenant_root)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, tenant_root / "current.json")
    except Exception:
        with suppress(FileNotFoundError):
            os.unlink(temporary_name)
        raise


def _load_published_snapshot(path: Path) -> APPolicySnapshotV1:
    raw = _read_json_object(path)
    try:
        snapshot = APPolicySnapshotV1.model_validate(raw)
    except ValidationError as exc:
        raise APPolicyBundleError(
            "POLICY_SNAPSHOT_CONFLICT",
            "Existing policy snapshot metadata is invalid",
        ) from exc
    canonical = snapshot.model_dump(mode="json", exclude={"publication_checksum"})
    if _checksum_object(canonical) != snapshot.publication_checksum:
        raise APPolicyBundleError(
            "POLICY_SNAPSHOT_CONFLICT",
            "Existing policy snapshot checksum is invalid",
        )
    return snapshot


def _canonical_json_lines(values: tuple[dict[str, Any], ...]) -> bytes:
    lines = [
        json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for item in values
    ]
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def _checksum_object(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return _checksum_bytes(raw)


def _checksum_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


__all__ = [
    "APPolicyBundleError",
    "CORPUS_MANIFEST_FILENAME",
    "LoadedAPPolicyBundle",
    "LoadedPolicyChunk",
    "LoadedPolicyDocument",
    "RULE_MANIFEST_FILENAME",
    "load_ap_policy_bundle",
    "publish_ap_policy_bundle",
]
