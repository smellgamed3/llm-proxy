FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy dependency files first (layer cache)
COPY pyproject.toml uv.lock ./

# Install production dependencies only, no project itself
RUN uv sync --frozen --no-dev --no-install-project

COPY common/ ./common/
COPY app/ ./app/
COPY config.yaml /etc/llm-proxy/config.yaml

RUN mkdir -p /data/logs

ENV UPSTREAM_URL=http://localhost:8080 \
    LISTEN_PORT=9090 \
    LOG_DIR=/data/logs \
    LOG_LEVEL=INFO \
    CONFIG_FILE=/etc/llm-proxy/config.yaml \
    # Use venv created by uv
    PATH="/app/.venv/bin:$PATH"

EXPOSE 9090

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9090/health')" || exit 1

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "9090", "--workers", "1", "--log-level", "info"]
