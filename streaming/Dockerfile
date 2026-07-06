# =============================================================================
# Dockerfile for the long-lived Finnhub WebSocket publisher.
# Designed for Azure Container Apps (or any OCI-compatible host) — keeps a
# persistent WS connection to wss://ws.finnhub.io and forwards every trade
# tick to Azure Service Bus. Auto-reconnects with exponential backoff.
#
# Image size is intentionally small (python:3.11-slim + layer-cached deps) so
# pulls are cheap during frequent CI/CD deploys.
#
# Health probe: this container exposes a tiny HTTP health endpoint on $PORT
# (default 8080) via a parallel thread. Azure Container Apps' liveness probe
# hits it; a 200 OK means "publisher process is alive" (it does NOT verify
# the Finnhub WebSocket is currently connected — reconnect is exponential
# backoff and we don't want a temporary outage to restart the container).
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
# shared env-var helpers re-used at runtime
COPY ingest/    /app/ingest/
COPY pyproject.toml /app/pyproject.toml
COPY README.md     /app/README.md

# Make sure the app code is owned by the non-root user.
RUN chown -R app:app /app
USER app

# Container Apps inject $PORT; the publisher exposes a tiny HTTP health
# server on this port alongside the long-lived WS connection. The
# entrypoint script starts the health server first so liveness probes succeed
# even if the WebSocket is briefly reconnecting.
EXPOSE 8080

# Default to the publisher entry point. Override `cmd` in Container App
# deployment if you need a different module/function.
CMD ["python", "-m", "streaming.container_entrypoint"]


# =============================================================================
# Optional: lightweight image linter via hadolint (run during CI, not build)
# =============================================================================
# hadolm ignore=DL3008
# We do NOT run unversioned apt installs in this Dockerfile; everything is
# pinned through the requirements.txt layer above.
