# syntax=docker/dockerfile:1.7

FROM python:3.11-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels .

FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    AP_POLICY_BUNDLE_DIR=/app/data/policies/accounts_payable/v1 \
    POLICY_SNAPSHOT_DIR=/app/data/policy-snapshots

RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid appuser --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app
COPY --from=builder /wheels /wheels
RUN python -m pip install /wheels/*.whl \
    && rm -rf /wheels

COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY business_alembic.ini ./business_alembic.ini
COPY business_migrations ./business_migrations
COPY data/policies ./data/policies
COPY scripts/seed_demo_database.py ./scripts/seed_demo_database.py
RUN mkdir -p /app/data/artifacts /app/data/policy-snapshots \
    && chown -R appuser:appuser /app

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).read()"]

CMD ["uvicorn", "copilot.bootstrap.api:app", "--host", "0.0.0.0", "--port", "8000"]
