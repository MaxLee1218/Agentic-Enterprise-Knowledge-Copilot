"""Thin executable wrapper for controlled AP policy publication."""

from copilot.bootstrap.policy_cli import policy_publish_main

if __name__ == "__main__":
    raise SystemExit(policy_publish_main())
