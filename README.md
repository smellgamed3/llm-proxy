# LLM Proxy

透明 HTTP 反向代理，用于记录 LLM API 请求/响应数据（含 SSE 流式），方便后续分析。

## 架构

```
Client → HTTPS Proxy → [llm-proxy :9090] → LLM Provider API
                             ↓
                        SQLite + JSONL 日志
```

## 快速开始

### Docker Compose（推荐）

```bash
# 设置上游地址
export UPSTREAM_URL=http://your-llm-service:8080

# 启动
docker compose up -d

# 查看日志
docker compose logs -f llm-proxy
```

### 本地运行

```bash
# 安装依赖（自动创建 .venv）
uv sync

UPSTREAM_URL=http://localhost:8080 \
LOG_DIR=./logs \
CONFIG_FILE=./config.yaml \
uv run uvicorn app.main:app --host 0.0.0.0 --port 9090
```

## 配置

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `UPSTREAM_URL` | `http://localhost:8080` | 下游 LLM 服务地址 |
| `LISTEN_PORT` | `9090` | 代理监听端口 |
| `LOG_DIR` | `/data/logs` | 日志存储目录 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `MAX_BODY_LOG_SIZE` | `10485760` | 单个 body 最大记录字节数 |
| `CONFIG_FILE` | `/etc/llm-proxy/config.yaml` | 配置文件路径 |

### 配置文件 (`config.yaml`)

配置文件用于设置路径过滤规则，控制哪些请求需要记录：

```yaml
recording:
  # 仅记录匹配的路径（为空则记录所有）
  include:
    - "/v1/chat/completions"
    - "/v1/embeddings"
    - pattern: "^/v1/(chat|completions)"
      regex: true

  # 排除的路径（优先于 include）
  exclude:
    - "/health"
    - "/ready"
    - "/metrics"
```

**过滤逻辑：**
1. 如果 `include` 非空 → 仅记录匹配 include 的路径
2. 匹配 `exclude` 的路径永远不记录（优先级高于 include）
3. 两者都为空 → 记录所有请求

## 数据存储

日志存储在 `LOG_DIR` 目录下：

- **`proxy.db`** — SQLite 数据库，存储请求元数据（时间、路径、状态码、模型名、耗时等）
- **`bodies.jsonl`** — JSONL 文件，存储完整的请求/响应 body

### 查询示例

```bash
# 进入容器查询
docker compose exec llm-proxy sqlite3 /data/logs/proxy.db

# 最近 10 条请求
SELECT id, timestamp, method, path, status_code, model, duration_ms
FROM requests ORDER BY timestamp DESC LIMIT 10;

# 按模型统计
SELECT model, COUNT(*) as cnt, AVG(duration_ms) as avg_ms
FROM requests GROUP BY model;

# 查看慢请求
SELECT * FROM requests WHERE duration_ms > 5000 ORDER BY duration_ms DESC;
```

## 与其他 Compose 服务集成

```yaml
services:
  # 你的 HTTPS 前端代理指向 llm-proxy:9090
  nginx:
    image: nginx
    # proxy_pass http://llm-proxy:9090;

  llm-proxy:
    build: ./llm-proxy
    environment:
      UPSTREAM_URL: http://llm-provider:8080
    volumes:
      - llm-proxy-logs:/data/logs
      - ./llm-proxy/config.yaml:/etc/llm-proxy/config.yaml:ro

  llm-provider:
    image: your-llm-service
```

## 健康检查

```bash
curl http://localhost:9090/health
# {"status": "ok"}
```
