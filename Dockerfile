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

# 启动脚本 + 可执行权限
COPY app/startup.sh /usr/local/bin/llm-proxy-start
RUN chmod +x /usr/local/bin/llm-proxy-start

ENV UPSTREAM_URL=http://localhost:8080 \
    LISTEN_PORT=9090 \
    LOG_DIR=/data/logs \
    LOG_LEVEL=INFO \
    CONFIG_FILE=/etc/llm-proxy/config.yaml \
    MAX_BODY_LOG_SIZE=10485760 \
    RECORDER_SOCKET=/var/run/llm-proxy/recorder.sock \
    UVICORN_WORKERS=4 \
    PATH="/app/.venv/bin:$PATH"

EXPOSE 9090

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9090/health')" || exit 1

CMD ["llm-proxy-start"]
