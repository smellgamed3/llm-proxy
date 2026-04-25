# LLM Proxy Analytics — 部署使用指南

> 关联文档：[技术架构](02-architecture.md) · [运维手册](05-operations.md)

## 1. 部署概览

系统由三个独立服务组成，可单独部署和扩缩：

| 服务 | 端口 | 职责 | 资源需求 |
|------|------|------|----------|
| `llm-proxy` | 9090 | 透明反向代理 + 原始数据录制 | 低 CPU，取决于流量 |
| `analyzer` | 无 | 后台分析 Worker | 低 CPU，峰值时中等 |
| `api` | 9091 | 查询 API + Dashboard | 低 CPU |

## 2. Docker Compose 部署（推荐）

### 2.1 前置条件

- Docker Engine ≥ 24.0
- Docker Compose V2
- 磁盘空间：视 LLM 流量而定，建议至少 10GB 可用空间

### 2.2 配置环境变量

```bash
# .env 文件（或直接 export）
UPSTREAM_URL=http://your-llm-service:8080    # 下游 LLM 服务基地址，不要追加 /v1
PROXY_PORT=9090                               # 代理监听端口
API_PORT=9091                                 # API/Dashboard 端口
LOG_LEVEL=INFO                                # 日志级别
ADMIN_KEY_HASH=0123456789abcdef0123456789abcdef  # Admin hash（SHA-256 前 32 位）
```

说明：客户端请求通常会访问 `/v1/chat/completions`、`/v1/embeddings` 等路径，因此 `UPSTREAM_URL` 只应配置到服务根地址。如果把 `/v1` 也写进 `UPSTREAM_URL`，代理会转发出重复路径，例如 `/v1/v1/chat/completions`。

### 2.3 docker-compose.yml

```yaml
services:
  # ---- 代理层 ----
  llm-proxy:
    build: .
    container_name: llm-proxy
    restart: unless-stopped
    ports:
      - "${PROXY_PORT:-9090}:9090"
    environment:
      UPSTREAM_URL: ${UPSTREAM_URL:-http://llm-provider:8080}
      LISTEN_PORT: "9090"
      LOG_DIR: /data/logs
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      CONFIG_FILE: /etc/llm-proxy/config.yaml
    volumes:
      - llm-data:/data/logs
      - ./config.yaml:/etc/llm-proxy/config.yaml:ro

  # ---- 分析 Worker ----
  analyzer:
    build:
      context: .
      dockerfile: Dockerfile.analyzer
    container_name: llm-analyzer
    restart: unless-stopped
    environment:
      RAW_DB: /data/logs/raw.db
      ANALYTICS_DB: /data/analytics/analytics.db
      BODIES_DIR: /data/logs/bodies
      PRICING_FILE: /etc/llm-proxy/pricing.yaml
      MODE: incremental
      INTERVAL: "5"
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
    volumes:
      - llm-data:/data/logs:ro              # 只读访问原始数据
      - llm-analytics:/data/analytics
      - ./pricing.yaml:/etc/llm-proxy/pricing.yaml:ro
    depends_on:
      - llm-proxy

  # ---- 查询 API + Dashboard ----
  api:
    build:
      context: .
      dockerfile: Dockerfile.api
    container_name: llm-api
    restart: unless-stopped
    ports:
      - "${API_PORT:-9091}:9091"
    environment:
      ANALYTICS_DB: /data/analytics/analytics.db
      RAW_DB: /data/logs/raw.db
      BODIES_DIR: /data/logs/bodies
      ADMIN_KEY_HASH: ${ADMIN_KEY_HASH:-}
      DASHBOARD_API_KEY: ${DASHBOARD_API_KEY:-}
      LISTEN_PORT: "9091"
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
    volumes:
      - llm-analytics:/data/analytics:ro
      - llm-data:/data/logs:ro
    depends_on:
      - analyzer

volumes:
  llm-data:
    driver: local
  llm-analytics:
    driver: local
```

### 2.4 启动

```bash
# 构建并启动所有服务
docker compose up -d

# 查看日志
docker compose logs -f

# 查看单个服务日志
docker compose logs -f llm-proxy
docker compose logs -f analyzer
docker compose logs -f api

# 查看服务状态
docker compose ps
```

### 2.5 访问

| 服务 | 地址 |
|------|------|
| 代理端点 | `http://localhost:9090` |
| 代理健康检查 | `http://localhost:9090/health` |
| Dashboard | `http://localhost:9091/` |
| API 文档 | `http://localhost:9091/docs` (Swagger UI) |
| API 基础路径 | `http://localhost:9091/api/` |

## 3. 本地开发部署

### 3.1 前置条件

- Python ≥ 3.12
- [uv](https://github.com/astral-sh/uv) 包管理器

### 3.2 安装依赖

```bash
cd llm-proxy
uv sync
```

### 3.3 启动代理

```bash
UPSTREAM_URL=http://localhost:8080 \
LOG_DIR=./logs \
CONFIG_FILE=./config.yaml \
uv run uvicorn app.main:create_app --factory --host 0.0.0.0 --port 9090
```

### 3.4 启动分析 Worker

```bash
# 增量模式（持续运行）
RAW_DB=./logs/raw.db \
ANALYTICS_DB=./logs/analytics.db \
BODIES_DIR=./logs/bodies \
PRICING_FILE=./pricing.yaml \
uv run python -m analyzer

# 全量重跑
uv run python -m analyzer --mode=full

# 范围重跑
uv run python -m analyzer --mode=range --since=2026-04-01 --until=2026-04-03
```

### 3.5 启动 API

```bash
ANALYTICS_DB=./logs/analytics.db \
RAW_DB=./logs/raw.db \
BODIES_DIR=./logs/bodies \
ADMIN_KEY_HASH=your_admin_hash \
uv run uvicorn api.app:create_app --factory --host 0.0.0.0 --port 9091
```

如需从真实 API key 计算 admin/scoped hash：

```bash
python3 - <<'PY'
import hashlib
print(hashlib.sha256(b'your-api-key').hexdigest()[:32])
PY
```

## 4. 配置文件说明

### 4.1 config.yaml — 代理配置

控制代理行为和录制过滤，详见项目现有文档。

```yaml
preserve_host: true
recording:
  include: []
  exclude:
    - "/health"
    - "/ready"
    - "/metrics"
```

### 4.2 pricing.yaml — 模型定价表

控制成本计算。价格单位：美元/百万 token。

```yaml
models:
  gpt-4o:
    input_per_1m: 2.50
    output_per_1m: 10.00
  gpt-4o-mini:
    input_per_1m: 0.15
    output_per_1m: 0.60
  # ... 更多模型

default:
  input_per_1m: 1.00
  output_per_1m: 5.00
```

**热更新**：修改 pricing.yaml 后不需要重启 analyzer，Worker 会在下一轮循环自动检测并重载。

## 5. 数据目录结构

```
/data/
├── logs/                           ← llm-data volume
│   ├── raw.db                      ← 原始元数据（含内联 body BLOB，v1.9.9+ 约 1GB）
│   └── bodies/
│       ├── 2026-04-03-14.jsonl     ← 按小时分片的 body 数据（双写，可清理释放空间）
│       ├── 2026-04-03-15.jsonl
│       └── manifest.jsonl          ← body 索引
│
├── analytics/                      ← llm-analytics volume
│   └── analytics.db                ← 分析结果（SQLite WAL）
│
└── backup/                         ← 备份目录（运维 cron 管理）
    ├── raw-2026-04-03.db.bak
    └── ...
```

## 6. 与现有系统集成

### 6.1 替换上游 LLM 地址

将 LLM 客户端的 base URL 指向代理：

```
# 原来
base_url = "http://llm-service:8080"

# 改为
base_url = "http://llm-proxy:9090"
```

代理完全透明，不修改请求/响应内容。

### 6.2 业务系统查询 API

业务系统通过 API 检索分析数据：

```python
import httpx

api = httpx.Client(base_url="http://llm-api:9091")

# 查询最近的对话
resp = api.get("/api/conversations", params={"model": "gpt-4o", "page_size": 10})
conversations = resp.json()

# 查询成本
resp = api.get("/api/costs/by-model", params={"date_from": "2026-04-01"})
costs = resp.json()
```

### 6.3 Grafana 集成（可选）

API 端点返回标准 JSON，可作为 Grafana JSON API 数据源使用。

## 7. 快速验证

部署完成后，按以下步骤验证全链路：

```bash
# 1. 验证代理可用
curl http://localhost:9090/health
# → {"status": "ok"}

# 2. 发一个测试请求（通过代理）
curl http://localhost:9090/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_KEY" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}'

# 3. 等待 5-10 秒（analyzer 轮询间隔）

# 4. 查看 Dashboard
open http://localhost:9091/

# 5. 通过 API 查询
curl http://localhost:9091/api/conversations?page_size=1
# → 应能看到刚才的请求

# 6. 直接查询原始数据验证
docker compose exec llm-proxy sqlite3 /data/logs/raw.db \
  "SELECT id, method, path, status_code FROM raw_requests ORDER BY seq DESC LIMIT 1;"
```

## 8. 内联 Body 存储迁移（v1.9.9+）

从 v1.9.9 起，body 数据 zlib 压缩后直接存入 raw.db 的 `request_body` / `response_body` BLOB 列。
新写入的数据自动双写到 JSONL 文件 + 内联 BLOB。对于已有的 body 数据，需运行一次性迁移脚本。

### 8.1 迁移步骤

```bash
# 1. 停止服务（避免迁移期间写入冲突）
docker compose down

# 2. 检查 raw.db 当前大小
ls -lh /data/logs/raw.db

# 3. 备份 raw.db（可选但推荐）
sqlite3 /data/logs/raw.db ".backup /data/backup/raw-pre-migrate.db.bak"

# 4. 运行迁移脚本
#    需要访问 raw.db 和 bodies/ 目录（可在宿主机或容器内执行）
python scripts/migrate_bodies_to_db.py /data/logs/raw.db /data/logs/bodies

# 5. 验证迁移结果
sqlite3 /data/logs/raw.db \
  "SELECT COUNT(*) AS total,
          SUM(CASE WHEN request_body IS NOT NULL THEN 1 ELSE 0 END) AS req_inline,
          SUM(CASE WHEN response_body IS NOT NULL THEN 1 ELSE 0 END) AS resp_inline
   FROM raw_requests;"

# 6. 启动新版本服务
docker compose up -d
```

### 8.2 迁移后效果

- **raw.db 增大**：112MB → ~1.1GB（含 ~1GB 压缩 body 数据）
- **bodies/ JSONL 文件**：可安全删除以释放 ~6.5GB 磁盘空间（确认迁移无误后）
- **全量同步加速**：本地 3x（49s → 16.6s），生产环境 10-30%+（取决于可迁移记录比例）

### 8.3 可选：清理 JSONL 文件

```bash
# 确认所有记录都已迁移后，可删除 JSONL shard 文件释放空间
rm -rf /data/logs/bodies/*.jsonl

# 保留 manifest（分析体量可见性）或一并删除
# rm -f /data/logs/bodies/manifest.jsonl

# 验证：全量重跑确认一切都正常
docker compose exec analyzer python -m analyzer --mode=full
```
