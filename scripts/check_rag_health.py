"""Standalone Enterprise RAG health-check entry point."""

from copilot.bootstrap.knowledge_cli import health_main

if __name__ == "__main__":
    raise SystemExit(health_main())
