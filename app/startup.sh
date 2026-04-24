#!/bin/sh
set -e

# 8C32G 多进程部署入口
# - 后台启动 recorder server（独立进程，独占 SQLite/JSONL 写）
# - 前台启动 uvicorn proxy workers（纯 HTTP/WS 转发）

RECORDER_SOCKET="${RECORDER_SOCKET:-/var/run/llm-proxy/recorder.sock}"

# 确保 socket 目录存在
mkdir -p "$(dirname "$RECORDER_SOCKET")"

# 确保日志目录存在
mkdir -p "${LOG_DIR:-/data/logs}"

echo "Starting recorder server..."
python -m app.recorder_server \
    --log-dir "${LOG_DIR:-/data/logs}" \
    --socket "${RECORDER_SOCKET}" \
    --max-body-log-size "${MAX_BODY_LOG_SIZE:-10485760}" &

RECORDER_PID=$!

# 等待 recorder socket 就绪
for i in $(seq 1 30); do
    if [ -S "$RECORDER_SOCKET" ]; then
        echo "Recorder ready (pid=$RECORDER_PID)"
        break
    fi
    sleep 0.5
done

# 清理函数
cleanup() {
    echo "Shutting down..."
    kill -TERM "$RECORDER_PID" 2>/dev/null || true
    wait "$RECORDER_PID" 2>/dev/null || true
    # 清理 socket
    rm -f "$RECORDER_SOCKET"
}
trap cleanup EXIT INT TERM

# 启动 proxy（前台运行）
echo "Starting proxy ($UVICORN_WORKERS workers)..."
exec uvicorn app.main:create_app --factory \
    --host "${LISTEN_HOST:-0.0.0.0}" \
    --port "${LISTEN_PORT:-9090}" \
    --workers "${UVICORN_WORKERS:-4}" \
    --log-level "${LOG_LEVEL:-info}"
