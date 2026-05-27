FROM python:3.14-slim AS builder

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# `apt-get upgrade` pulls Debian security patches that python:3.13-slim
# doesn't always ship at the latest level (CVEs in libc6, etc.). Without
# this, Trivy flags HIGH/CRITICAL OS CVEs every build.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
# Upgrade pip before installing uv — the python:3.13-slim base ships a pip
# that lags behind CVE patches (CVE-2026-6357 was the last find). Trivy scans
# the runtime image, but pip leaks into runtime via the base image regardless
# of where it's used, so we upgrade in both stages.
RUN pip install --upgrade pip && pip install uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev


FROM python:3.14-slim AS runtime

ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
# Match the builder's `apt-get upgrade` so the runtime image has Debian
# security patches applied. The runtime image is what actually ships to
# prod, so it's the one Trivy scans.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*
# pip is shipped by the python:3.13-slim base image even though we don't
# install anything with it here — Trivy scans it and flags any CVE. Upgrade
# to the latest pip in the runtime image too.
RUN pip install --upgrade pip

WORKDIR /app
COPY --from=builder /app /app
COPY migrations ./migrations
COPY alembic.ini ./

# Run as non-root user
RUN groupadd -r appuser && useradd -r -u 1001 -g appuser appuser \
    && chown -R appuser:appuser /app
USER appuser

# Single image is used for both `app` and `ingestor` services. Each Cloud Run
# service overrides CMD to point at its own uvicorn module. The exposed port
# below is the default for `app`; the ingestor service runs on the same port
# (Cloud Run sets $PORT and the CMD args adapt).
EXPOSE 8000
CMD ["uvicorn", "webhook_inspector.web.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
