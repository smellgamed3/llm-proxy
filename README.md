# LLM Proxy

透明 HTTP 反向代理 + 流量分析系统，用于记录和分析 LLM API 请求/响应数据（含 SSE 流式和 WebSocket）。

提供提示词提取、Token 用量统计、成本计算、延迟分析、成功/失败分类等能力，并通过 RESTful API 和 Dashboard 对外提供查询服务。

当前版本：`1.9.10`

## 质量基线

- 统一日志配置：`proxy`、`analyzer`、`api` 均支持 `LOG_FORMAT=json`
- API 默认启用基础限流，避免 dashboard / query endpoint 被瞬时打爆
- Dashboard 公共壳层统一收敛到 `api/static/app.js`，减少多页面重复模板
- Dashboard key/hash 管理支持多 key 激活、别名二次编辑与亮暗主题切换
- Dashboard 全页面已由 agent-browser 集成测试覆盖，包含 scoped/admin 两类鉴权视角
- CI 已覆盖多 Python 版本测试与镜像构建发布

## Dashboard 能力概览

- 统一侧边栏导航 + 顶部全局信息栏
- 对话详情弹窗：折叠分区、Tool 参数展开、Skill 统计
- Token 分析：输入/输出、缓存、推理、内容构成
- 性能分析：tok/s、吞吐量、I/O 比、成本效率
- 内容分析：消息结构、字符词数、代码块检测、输入输出比
- 请求配置分析：temperature/max_tokens/top_p/tool_choice 等参数可视化

## 架构

```
 Client ───→ [llm-proxy :9090] ───→ Upstream LLM
                    │
                    │ sync write (<1ms)
                    ▼
       ┌────────────────────────────┐
       │   Raw Store (raw.db +      │
       │   bodies/*.jsonl)          │
       └─────────────┬──────────────┘
                     │ async poll (5s)
                     ▼
       ┌────────────────────────────┐
       │   Analyzer Worker          │
       │   (提取/成本/指纹/分类)     │
       └─────────────┬──────────────┘
                     │
                     ▼
       ┌────────────────────────────┐
       │   Analytics (analytics.db) │
       └─────────────┬──────────────┘
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
    Dashboard :9091       Query API :9091
    (管理/调试)           (业务系统集成)
```

三个独立进程：**代理**（录制）→ **分析 Worker**（提取）→ **API + Dashboard**（查询），互不影响。

## 快速开始

### Docker Compose（推荐）

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env，至少设置上游地址
# 注意：这里填的是上游服务基地址，不要追加 /v1
# UPSTREAM_URL=http://your-llm-service:8080

# 启动全部服务（代理 + 分析 + API）
docker compose up -d

# 查看日志
docker compose logs -f
```

`docker compose` 会自动读取项目根目录的 `.env`。常用变量包括 `UPSTREAM_URL`、`PROXY_PORT`、`API_PORT`、`ADMIN_KEY_HASH`、`DASHBOARD_API_KEY`、`LOG_LEVEL`、`LOG_FORMAT`、`ANALYZER_INTERVAL`、`ANALYZER_BATCH_SIZE`、`API_RATE_LIMIT_ENABLED`、`API_RATE_LIMIT_MAX_REQUESTS`、`API_RATE_LIMIT_WINDOW_SECONDS` 和 `TRAEFIK_HOST`。

如果启用了 hash 鉴权，可用下面命令从真实 API key 计算 dashboard/API 使用的 hash：

```bash
python3 - <<'PY'
import hashlib
print(hashlib.sha256(b'your-api-key').hexdigest()[:32])
PY
```

服务启动后：

| 服务 | 地址 |
|------|------|
| 代理端点 | `http://localhost:9090` |
| 健康检查 | `http://localhost:9090/health` |
| Dashboard | `http://localhost:9091/` |
| API 文档 | `http://localhost:9091/docs` |

### 纯 API 调用

如果你不使用 Dashboard，只想把本项目作为代理与查询 API 接入到自己的程序里，直接看 [docs/08-api-client-usage.md](docs/08-api-client-usage.md)。

这个文档覆盖：

- 代理调用方式：如何把业务请求发到 `:9090`
- 查询调用方式：如何用 `key_hashes` 访问 `:9091/api/*`
- `curl`、Python、JavaScript 三种最小示例
- Admin 与 scoped 两种鉴权模式区别
- 常见 401 / `/v1/v1/...` / 非安全上下文加 key 失败等排障点

### 本地开发

```bash
# 安装依赖
uv sync

# 启动代理
UPSTREAM_URL=http://localhost:8080 LOG_DIR=./logs CONFIG_FILE=./config.yaml \
uv run uvicorn app.main:create_app --factory --host 0.0.0.0 --port 9090

# 启动分析 Worker（另一个终端）
RAW_DB=./logs/raw.db ANALYTICS_DB=./logs/analytics.db \
BODIES_DIR=./logs/bodies PRICING_FILE=./pricing.yaml \
uv run python -m analyzer

# 启动 API（另一个终端）
ANALYTICS_DB=./logs/analytics.db RAW_DB=./logs/raw.db BODIES_DIR=./logs/bodies \
ADMIN_KEY_HASH=your_admin_hash \
uv run uvicorn api.app:create_app --factory --host 0.0.0.0 --port 9091
```

## 配置

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `UPSTREAM_URL` | `http://localhost:8080` | 下游 LLM 服务基地址，不要追加 `/v1` |
| `LISTEN_PORT` | `9090` | 代理监听端口 |
| `LOG_DIR` | `/data/logs` | 原始数据存储目录 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `MAX_BODY_LOG_SIZE` | `10485760` | 单个 body 最大记录字节数 |
| `PRESERVE_HOST` | `true` | 是否将客户端 `Host` 原样透传给下游 |
| `CONFIG_FILE` | `/etc/llm-proxy/config.yaml` | 配置文件路径 |
| `ADMIN_KEY_HASH` | 空 | Admin hash，匹配后可查看全部数据和管理接口 |
| `DASHBOARD_API_KEY` | 空 | 旧版 Bearer 管理口令，兼容模式使用 |
| `LOG_FORMAT` | `text` | `text` 或 `json`，控制服务日志格式 |
| `API_RATE_LIMIT_ENABLED` | `true` | 是否启用 API 限流 |
| `API_RATE_LIMIT_MAX_REQUESTS` | `300` | 时间窗口内允许的最大 API 请求数 |
| `API_RATE_LIMIT_WINDOW_SECONDS` | `60` | API 限流窗口大小（秒） |

### 配置文件 (`config.yaml`)

控制代理行为和录制过滤规则：

```yaml
preserve_host: true

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

### 模型定价 (`pricing.yaml`)

用于成本计算，价格单位为美元/百万 token：

```yaml
models:
  gpt-4o:
    input_per_1m: 2.50
    output_per_1m: 10.00
  gpt-4o-mini:
    input_per_1m: 0.15
    output_per_1m: 0.60

default:
  input_per_1m: 1.00
  output_per_1m: 5.00
```

修改后无需重启 Worker，自动热更新。

### 代理兼容规则

贴近 Nginx / Caddy / Traefik 等成熟反向代理的行为：

- 透传原始 `Host`（可通过 `PRESERVE_HOST=false` 关闭）
- 补齐 `Forwarded`、`X-Forwarded-*`、`X-Real-IP`
- 移除 hop-by-hop 头，以及 `Connection` 声明的扩展逐跳头
- 保留原始百分号编码路径（例如 `%2F` 不会被错误解码）
- 重写指向下游真实地址的绝对 `Location` 跳转为代理对外地址
- 保留重复响应头，尤其是多个 `Set-Cookie`

## 数据存储

### 原始数据（Raw Store）

存储在 `LOG_DIR` 目录下，由代理层写入，**不做任何 JSON 解析**：

- **`raw.db`** — SQLite 数据库（WAL 模式），存储请求/响应元数据
- **`bodies/*.jsonl`** — 按小时分片的 JSONL 文件，存储完整的请求/响应 body
- **`bodies/manifest.jsonl`** — body 文件索引，确保数据引用不丢失

### 分析数据（Analytics Store）

由 Analyzer Worker 异步生成，可随时删除并从原始数据重建：

- **`analytics.db`** — SQLite 数据库，存储结构化分析结果（模型、token、成本、延迟、提示词指纹等）

### 查询示例

```bash
# 查询原始数据
docker compose exec llm-proxy sqlite3 /data/logs/raw.db \
  "SELECT seq, method, path, status_code, duration_ms
   FROM raw_requests ORDER BY seq DESC LIMIT 10;"

# 查询分析数据
docker compose exec api sqlite3 /data/analytics/analytics.db \
  "SELECT model, COUNT(*) as cnt, SUM(cost_usd) as cost,
          AVG(duration_ms) as avg_ms
   FROM conversations GROUP BY model;"
```

## 分析 Worker

独立进程，异步从原始数据提取结构化信息。支持三种运行模式：

```bash
# 增量模式（默认，守护进程持续运行）
docker compose exec analyzer python -m analyzer

# 全量重跑（清空 analytics.db，从头重建）
docker compose exec analyzer python -m analyzer --mode=full

# 范围重跑（指定时间段）
docker compose exec analyzer python -m analyzer --mode=range \
  --since=2026-04-01 --until=2026-04-03
```

## Query API

RESTful JSON API，供 Dashboard 和业务系统使用：

| 端点 | 说明 |
|------|------|
| `GET /api/overview` | 总览（请求量、成功率、总成本、平均延迟） |
| `GET /api/conversations` | 对话列表（支持 model/status/date/关键词过滤和分页） |
| `GET /api/conversations/:id` | 对话详情（含完整 prompt + response） |
| `GET /api/conversations/:id/raw` | 回溯原始请求/响应 body |
| `GET /api/costs/summary` | 成本总览 |
| `GET /api/costs/by-model` | 按模型汇总成本和 token |
| `GET /api/costs/daily` | 每日成本趋势 |
| `GET /api/latency/summary` | 延迟分析（P50/P95/P99） |
| `GET /api/prompts/templates` | 提示词模板列表 |
| `GET /api/models/usage` | 按模型维度统计 |
| `GET /api/errors/summary` | 错误概览 |
| `GET /api/errors/recent` | 最近错误列表 |
| `POST /api/admin/analyzer/rerun` | 触发一次重分析/补跑 |

更完整的纯 API 集成说明见 [docs/08-api-client-usage.md](docs/08-api-client-usage.md)。

完整 API 文档：`http://localhost:9091/docs`

## 与其他 Compose 服务集成

```yaml
services:
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
# 代理
curl http://localhost:9090/health
# → {"status": "ok"}

# 分析 Worker 状态
curl http://localhost:9091/api/admin/analyzer/status \
  -H 'Authorization: Bearer <ADMIN_KEY_HASH>'
# → {"watermark_seq": 12345, "records_processed": 12345, ...}
```

## 生产镜像 Smoke Test

当使用 `docker-compose.yml`（生产编排）拉取远端 GHCR 镜像启动后，可用一条脚本做端到端验收：

```bash
PROVIDER_API_KEY=sk-xxx \
PROVIDER_MODEL=gpt-4o-mini \
ADMIN_KEY_HASH=<admin-hash> \
bash scripts/smoke_prod.sh
```

默认会检查：

- 代理健康接口
- scoped 总览接口
- 通过代理发送一条真实请求
- 等待 analyzer 处理后检查 scoped conversations
- 验证 `/api/conversations/{id}/raw` 回溯出的 request/response body
- 可选验证 admin 管理接口

如果你的宿主机端口不是默认值，可显式指定：

```bash
PROXY_PORT=19090 API_PORT=19091 ANALYZER_WAIT_SECONDS=8 \
PROVIDER_API_KEY=sk-xxx ADMIN_KEY_HASH=<admin-hash> \
bash scripts/smoke_prod.sh
```

## 文档

详细设计和运维文档见 [docs/](docs/README.md)：

- [项目需求规格](docs/01-requirements.md)
- [技术架构设计](docs/02-architecture.md)
- [开发测试验收计划](docs/03-development-plan.md)
- [部署使用指南](docs/04-deployment.md)
- [发布维护运维手册](docs/05-operations.md)
