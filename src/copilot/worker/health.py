"""Dependency-only Worker readiness probe for container health checks."""

from copilot.bootstrap.worker import build_worker_application
from copilot.config import get_settings


def main() -> int:
    """Return success when persistence, Queue, checkpoint, and business dependencies are ready."""
    try:
        with build_worker_application(get_settings()) as application:
            return 0 if application.runtime.accepting_work else 1
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
