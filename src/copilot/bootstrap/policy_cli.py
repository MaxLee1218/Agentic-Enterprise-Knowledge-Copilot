"""CLI composition for controlled AP policy validation and snapshot publication."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from copilot.config import ConfigurationError, get_settings
from copilot.tools.knowledge import (
    APPolicyBundleError,
    load_ap_policy_bundle,
    publish_ap_policy_bundle,
)

EXIT_CONFIGURATION = 2
EXIT_VALIDATION = 3
EXIT_PUBLICATION = 4


def policy_publish_main(argv: Sequence[str] | None = None) -> int:
    """Validate exact rule/document bindings and optionally publish an immutable snapshot."""
    arguments = _parser().parse_args(argv)
    try:
        settings = get_settings()
        bundle_path = (arguments.bundle_dir or settings.ap_policy_bundle_dir).resolve()
        bundle = load_ap_policy_bundle(
            bundle_path,
            expected_tenant_id=arguments.tenant_id,
        )
        if arguments.validate_only:
            payload = {
                "validated": True,
                "published": False,
                "tenant_id": bundle.corpus.tenant_id,
                "namespace": bundle.corpus.namespace,
                "collection_id": bundle.corpus.collection_id,
                "policy_profile": bundle.corpus.policy_profile,
                "rule_set_version": bundle.rule_manifest.rule_set_version,
                "manifest_checksum": bundle.rule_manifest.manifest_checksum,
                "corpus_checksum": bundle.corpus.corpus_checksum,
                "payload_checksum": bundle.payload_checksum,
                "document_count": len(bundle.documents),
                "chunk_count": sum(len(item.chunks) for item in bundle.documents),
                "binding_count": len(bundle.rule_manifest.rules),
            }
        else:
            output_path = (arguments.output_dir or settings.policy_snapshot_dir).resolve()
            snapshot = publish_ap_policy_bundle(
                bundle,
                output_path,
                index_revision=arguments.index_revision,
            )
            payload = {
                "validated": True,
                "published": True,
                **snapshot.model_dump(mode="json"),
                "snapshot_location": str(output_path / snapshot.tenant_id / snapshot.snapshot_id),
            }
    except (ConfigurationError, ValueError) as exc:
        return _print_error("CONFIGURATION_INVALID", str(exc), EXIT_CONFIGURATION)
    except APPolicyBundleError as exc:
        publication_codes = {
            "POLICY_INDEX_REVISION_INVALID",
            "POLICY_PUBLICATION_FAILED",
            "POLICY_PUBLICATION_TIME_INVALID",
            "POLICY_SNAPSHOT_CONFLICT",
        }
        exit_code = EXIT_PUBLICATION if exc.code in publication_codes else EXIT_VALIDATION
        return _print_error(exc.code, str(exc), exit_code)

    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def policy_publish_entrypoint() -> int:
    """Console-script adapter for AP policy publication."""
    return policy_publish_main()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the frozen AP policy corpus and atomically publish one tenant-bound "
            "immutable RAG payload snapshot."
        )
    )
    parser.add_argument("--bundle-dir", type=Path, help="Controlled AP policy bundle directory.")
    parser.add_argument("--output-dir", type=Path, help="Immutable snapshot publication root.")
    parser.add_argument(
        "--tenant-id",
        default="TENANT-DEMO",
        help="Expected tenant; a mismatch fails closed.",
    )
    parser.add_argument(
        "--index-revision",
        default="initial",
        help="Controlled RAG index generation identifier.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Run checksum, date, namespace and binding gates without publishing.",
    )
    return parser


def _print_error(code: str, message: str, exit_code: int) -> int:
    print(json.dumps({"error_code": code, "message": message}, sort_keys=True))
    return exit_code


__all__ = ["policy_publish_entrypoint", "policy_publish_main"]
