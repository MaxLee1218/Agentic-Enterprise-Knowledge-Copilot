"""New synthetic validation inputs; this package does not execute product tasks."""

from evaluation.supplier_quality_validation.bundle import (
    DEFAULT_BUNDLE,
    ValidationBundle,
    verify_bundle,
)

__all__ = ["DEFAULT_BUNDLE", "ValidationBundle", "verify_bundle"]
