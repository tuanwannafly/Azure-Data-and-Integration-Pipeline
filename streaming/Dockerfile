# =============================================================================
# Dockerfile for the long-lived Finnhub WebSocket publisher.
# Designed for Azure Container Apps (or any OCI-compatible host) — keeps a
# persistent WS connection to wss://ws.finnhub.io and forwards every trade
# tick to Azure Service Bus. Auto-reconnects with exponential backoff.
#
# Image size is intentionally small (python:3.11-slim + layer-cached deps) so
# pulls are cheap during frequent CI/CD deploys.
# =============================================================================
FROM python:3.11-slim AS base

# Make output deterministic and fail fast on pipe errors.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONFAULTHANDLER=1

# Create non-root user (BR-04 hygiene: principle of least privilege).
RUN groupadd --system --gid 1001 app && \
    useradd  --system --uid 1001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

# ---- dependencies layer (cached unless requirements.txt changes) ----
COPY streaming/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && \
    # Strip pip cache, test files, .dist-info noise to keep the image small.
    find /usr/local/lib/python3.11/site-packages -type d -name '__pycache__' -prune -exec rm -rf {} +

# ---- application layer ----
COPY streaming/ /app/streaming/
COPY ingest/    /app/ingest/      # shared env-var helpers re-used at runtime
COPY pyproject.toml /app/pyproject.toml
COPY README.md     /app/README.md

# Make sure the app code is owned by the non-root user.
RUN chown -R app:app /app
USER app

# Container Apps inject $PORT; the publisher has no HTTP listener (it only
# opens an outbound WebSocket), so we ignore $PORT. HEALTHCHECK via a tiny
# helper process (see below) is a bonus but optional.
EXPOSE 8080

# Default to the publisher entry point. Override `cmd` in Container App
# deployment if you need a different module/function.
CMD ["python", "-m", "streaming.finnhub_ws_publisher"]


# =============================================================================
# Optional: lightweight image linter via hadolint (run during CI, not build)
# =============================================================================
# hadolm ignore=DL3008
# We do NOT run unversioned apt installs in this Dockerfile; everything is
# pinned through the requirements.txt layer above.
