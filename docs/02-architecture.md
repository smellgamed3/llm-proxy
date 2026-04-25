# LLM Proxy Analytics — 技术架构设计

> 关联文档：[需求规格](01-requirements.md) · [开发计划](03-development-plan.md) · [部署指南](04-deployment.md) · [运维手册](05-operations.md)

## 1. 系统架构总览

```
 Client ───→ [llm-proxy :9090] ───→ Upstream LLM
                    │
                    │ sync write (<1ms overhead)
                    ▼
       ┌────────────────────────────────┐
       │        Raw Store (不可变)       │
       │  raw.db  (SQLite, WAL mode)    │   ← 全量原始元数据
       │  bodies/YYYY-MM-DD-HH.jsonl   │   ← 按小时分片的原始 body
       │  bodies/manifest.jsonl         │   ← body 文件索引
       └───────────────┬────────────────┘
                       │
          ┌────────────┼─────────────┐
          │            │             │
     全量重跑     增量轮询(5s)    范围重跑
          │            │             │
          ▼            ▼             ▼
       ┌────────────────────────────────┐
       │        Analyzer Worker         │   ← 独立进程
       │  - Extractor 插件体系          │
       │  - Token & Cost 计算           │
       │  - 提示词指纹/分类             │
       │  - Watermark 游标管理          │
       └───────────────┬────────────────┘
                       │ write
                       ▼
       ┌────────────────────────────────┐
       │    Analytics Store (可重建)     │
       │  analytics.db (SQLite)         │
       │  - conversations               │
       │  - prompt_templates            │
       │  - daily_stats                 │
       │  - watermark                   │
       └───────────────┬────────────────┘
                       │ read-only
            ┌──────────┴──────────┐
            ▼                     ▼
     ┌─────────────┐    ┌───────────────┐
     │  Dashboard   │    │  Query API    │
     │  (内置 Web)  │    │  (RESTful)    │
     │  管理/调试    │    │  对外业务系统  │
     └─────────────┘    └───────────────┘
```

### 核心设计原则

1. **录制层零侵入**：代理热路径只做原样存储，不做 JSON 解析或业务判断
2. **原始 ↔ 加工完全隔离**：raw.db 和 analytics.db 物理分离，analytics 可随时删除重建
3. **三种分析模式**：增量（实时）、全量（重建）、范围（补跑），通过 seq 游标统一管理
4. **三进程独立**：proxy、analyzer、api 可独立部署、重启、扩缩

## 2. 组件详细设计

### 2.1 Raw Store — 原始数据层

#### 2.1.1 raw.db 表结构

```sql
-- ============================================================
-- raw_requests: HTTP 请求/响应原始记录
-- ============================================================
CREATE TABLE raw_requests (
    -- 标识
    id                TEXT PRIMARY KEY,              -- UUID v4
    seq               INTEGER NOT NULL UNIQUE,       -- 单调递增，由 AUTOINCREMENT trigger 或应用层生成

    -- 时间
    timestamp         TEXT NOT NULL,                 -- ISO-8601 UTC
    duration_ms       REAL,                          -- 端到端延迟（含上游响应时间）

    -- HTTP 元信息（原样保存，不解析）
    method            TEXT NOT NULL,
    path              TEXT NOT NULL,
    query_string      TEXT,
    request_headers   TEXT,                          -- JSON string，原始 headers
    response_headers  TEXT,                          -- JSON string
    status_code       INTEGER,

    -- Body 引用（指向 bodies/*.jsonl 中的 ref key）
    request_body_ref  TEXT,
    response_body_ref TEXT,

    -- 内联 Body（v1.9.9+，zlib 压缩后直接存储，消除 FUSE 文件 I/O 瓶颈）
    request_body      BLOB,                          -- zlib(request_body_str)
    response_body     BLOB,                          -- zlib(response_body_str)

    -- 传输特征
    is_stream         INTEGER DEFAULT 0,             -- 1=SSE streaming
    request_body_size  INTEGER,                      -- 字节数
    response_body_size INTEGER,                      -- 字节数

    -- 客户端信息
    client_ip         TEXT,
    client_port       INTEGER,

    -- 连接信息
    upstream_url      TEXT,                          -- 实际转发的完整 URL
    error             TEXT,                          -- 代理层错误（连接失败、超时等）

    -- 元数据
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_raw_seq ON raw_requests(seq);
CREATE INDEX idx_raw_timestamp ON raw_requests(timestamp);
CREATE INDEX idx_raw_path ON raw_requests(path);
CREATE INDEX idx_raw_status ON raw_requests(status_code);

-- ============================================================
-- raw_ws_connections: WebSocket 连接记录
-- ============================================================
CREATE TABLE raw_ws_connections (
    id                TEXT PRIMARY KEY,
    seq               INTEGER NOT NULL UNIQUE,
    timestamp         TEXT NOT NULL,
    path              TEXT NOT NULL,
    query_string      TEXT,
    request_headers   TEXT,
    subprotocol       TEXT,
    closed_at         TEXT,
    duration_ms       REAL,
    message_count     INTEGER DEFAULT 0,
    client_ip         TEXT,
    client_port       INTEGER,
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_raw_ws_seq ON raw_ws_connections(seq);
CREATE INDEX idx_raw_ws_timestamp ON raw_ws_connections(timestamp);

-- ============================================================
-- raw_ws_messages: WebSocket 消息记录
-- ============================================================
CREATE TABLE raw_ws_messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id     TEXT NOT NULL,
    direction         TEXT NOT NULL,                  -- client_to_server / server_to_client
    message_type      TEXT NOT NULL,                  -- text / binary
    data              TEXT,                           -- 原始消息内容
    data_size         INTEGER,                        -- 字节数
    timestamp         TEXT NOT NULL,
    FOREIGN KEY (connection_id) REFERENCES raw_ws_connections(id)
);

CREATE INDEX idx_raw_ws_msg_conn ON raw_ws_messages(connection_id);
```

#### 2.1.2 seq 序号生成策略

使用应用层线程安全的自增计数器，而非 SQLite AUTOINCREMENT（因为 `id` 是 TEXT 类型的 UUID，不能用 rowid 自增）：

```python
class SeqGenerator:
    """线程安全的单调递增序号生成器，启动时从 DB 恢复。"""
    def __init__(self, db_path: str, table: str):
        # 启动时读取 MAX(seq)
        self._counter = self._load_max_seq(db_path, table)
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            self._counter += 1
            return self._counter
```

#### 2.1.3 Body 存储（双写策略 v1.9.9+）

从 v1.9.9 起，body 数据采用**双写策略**，同时写入两种存储：

1. **JSONL 文件**（向后兼容）：写入 `bodies/YYYY-MM-DD-HH.jsonl` + `manifest.jsonl`
2. **SQLite 内联 BLOB**（主读路径）：zlib 压缩后存入 `request_body` / `response_body` 列

**设计原因**：Docker/OrbStack 的 FUSE 文件系统使 JSONL body 读取成为主要性能瓶颈（占总处理时间 58%）。内联 BLOB 将 body 读取从文件 I/O 变为 SQLite 行内读，大幅减少 FUSE 调用。

**读取优先级**：分析 Worker 优先读取内联 BLOB（`decompress_body`），仅旧记录（无 BLOB 数据）回退到文件读取（`BodyReader` 文件句柄缓存 → manifest → shard scan）。

**迁移工具**：提供一次性迁移脚本 `scripts/migrate_bodies_to_db.py`，将现有 JSONL shard 数据写入 BLOB 列。

**压缩比**（实测 67K records）：
- 请求体（JSON）：~91%（144.7KB → 13.5KB avg）
- 响应体（JSON）：~87%（10.4KB → 1.4KB avg）
- 原始 body 总量：~6.5GB → 压缩后约 1GB 内联在 raw.db 中

**数据目录结构**：

```
/data/logs/
├── raw.db                        ← 含内联 body BLOB（v1.9.9 迁移后约 1.1GB）
└── bodies/                       ← JSONL 文件（可删除以释放空间）
    ├── 2026-04-03-14.jsonl
    ├── 2026-04-03-15.jsonl
    └── manifest.jsonl
```

**JSONL 行格式**（不变，用于向后兼容）：
```json
{"ref": "uuid:request", "timestamp": "...", "data": "..."}
{"ref": "uuid:response", "timestamp": "...", "data": "..."}
```

**Manifest 行格式**（每次写 body 时追加）：
```json
{"ref": "uuid:request", "file": "2026-04-03-14.jsonl", "offset": 0, "length": 1234}
```

**分片策略**（不变）：
- 按 UTC 小时分文件：`YYYY-MM-DD-HH.jsonl`
- 当前小时文件以 append 模式打开，每小时轮转
- manifest 同步追加，记录 ref → 文件 + 偏移量映射

### 2.2 Analyzer Worker — 分析引擎

#### 2.2.1 项目结构

```
analyzer/
├── __init__.py
├── __main__.py              ← CLI 入口：python -m analyzer
├── worker.py                ← 主循环：轮询 + 游标管理
├── body_reader.py           ← 从 JSONL / SQLite BLOB 读取 body 内容
├── extractors/
│   ├── __init__.py
│   ├── base.py              ← Extractor 抽象基类
│   ├── openai_compat.py     ← OpenAI / 兼容 API 格式
│   ├── anthropic.py         ← Anthropic Messages API
│   └── generic.py           ← 通用兜底（只提取 HTTP 级别信息）
├── cost.py                  ← 成本计算器
├── fingerprint.py           ← 提示词指纹生成
├── store.py                 ← analytics.db 读写
└── config.py                ← Worker 配置
```

#### 2.2.2 Extractor 抽象接口

```python
@dataclass
class ExtractionResult:
    provider: str | None         # openai / anthropic / unknown
    model: str | None
    request_type: str | None     # chat / completion / embedding / realtime

    # 提示词
    system_prompt: str | None
    user_prompt: str | None
    messages_count: int | None
    has_tools: bool
    tools_list: list[str] | None
    temperature: float | None
    max_tokens: int | None

    # 响应
    assistant_response: str | None
    finish_reason: str | None

    # Token
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None

    # 状态
    status: str                  # success / error / timeout / rate_limited
    error_type: str | None
    error_message: str | None


class BaseExtractor(ABC):
    """所有 Extractor 的基类。"""

    @abstractmethod
    def can_handle(self, path: str, method: str, request_headers: dict) -> bool:
        """判断是否能处理这条请求。"""

    @abstractmethod
    def extract(
        self,
        raw_record: dict,           # raw_requests 行
        request_body: str | None,
        response_body: str | None,
    ) -> ExtractionResult:
        """从原始数据提取结构化信息。"""
```

**Extractor 选择顺序**（优先级从高到低）：
1. `OpenAICompatExtractor` — 路径匹配 `/v1/chat/completions`, `/v1/completions`, `/v1/embeddings` 等
2. `AnthropicExtractor` — 路径匹配 `/v1/messages` 或 request header 含 `x-api-key` + `anthropic-version`
3. `GenericExtractor` — 兜底，只提取 HTTP 级别信息

#### 2.2.3 Worker 主循环

```python
class AnalyzerWorker:
    def __init__(self, config):
        self.raw_db = connect(config.raw_db_path)      # 只读
        self.analytics_store = AnalyticsStore(config)   # 读写
        self.body_reader = BodyReader(config.bodies_dir)
        self.extractors = [OpenAICompatExtractor(), AnthropicExtractor(), GenericExtractor()]
        self.cost_calculator = CostCalculator(config.pricing_path)
        self.fingerprinter = Fingerprinter()

    def run(self, mode: str = "incremental"):
        if mode == "full":
            self.analytics_store.reset()     # 清空所有分析数据
            start_seq = 0
        elif mode == "range":
            start_seq = self._seq_for_timestamp(self.config.since)
        else:
            start_seq = self.analytics_store.get_watermark()

        while True:
            batch = self._fetch_unprocessed(start_seq, batch_size=100)
            if not batch:
                if mode != "incremental":
                    break                    # 非增量模式处理完即退出
                time.sleep(self.config.interval)
                continue

            for record in batch:
                result = self._process_one(record)
                self.analytics_store.upsert_conversation(record, result)
                start_seq = record["seq"]

            self.analytics_store.update_watermark(start_seq)
            self._maybe_refresh_daily_stats()

    def _process_one(self, record: dict) -> ExtractionResult:
        # 优先读取内联 BLOB（v1.9.9+），回退 JSONL 文件读取
        req_body = None
        if record.get("request_body"):
            req_body = decompress_body(record["request_body"])
        elif record.get("request_body_ref"):
            req_body = self.body_reader.read(record["request_body_ref"])

        resp_body = None
        if record.get("response_body"):
            resp_body = decompress_body(record["response_body"])
        elif record.get("response_body_ref"):
            resp_body = self.body_reader.read(record["response_body_ref"])

        for extractor in self.extractors:
            if extractor.can_handle(record["path"], record["method"], ...):
                result = extractor.extract(record, req_body, resp_body)
                break
        else:
            result = self.extractors[-1].extract(record, req_body, resp_body)  # generic

        # 计算成本
        result.cost_usd = self.cost_calculator.calculate(
            result.model, result.prompt_tokens, result.completion_tokens
        )

        # 生成指纹
        result.template_id = self.fingerprinter.fingerprint(result.system_prompt)

        return result
```

#### 2.2.4 三种运行模式

| 模式 | 命令 | 行为 |
|------|------|------|
| 增量（默认） | `python -m analyzer` | 守护进程，持续轮询 `seq > watermark`，5s 间隔 |
| 全量重跑 | `python -m analyzer --mode=full` | 清空 analytics.db，从 seq=0 重新处理所有数据 |
| 范围重跑 | `python -m analyzer --mode=range --since=2026-04-01 --until=2026-04-03` | 按时间范围筛选处理，不影响 watermark |

#### 2.2.5 成本计算

```yaml
# pricing.yaml
models:
  gpt-4o:
    input_per_1m: 2.50
    output_per_1m: 10.00
  gpt-4o-mini:
    input_per_1m: 0.15
    output_per_1m: 0.60
  gpt-4.1:
    input_per_1m: 2.00
    output_per_1m: 8.00
  gpt-4.1-mini:
    input_per_1m: 0.40
    output_per_1m: 1.60
  gpt-4.1-nano:
    input_per_1m: 0.10
    output_per_1m: 0.40
  o3:
    input_per_1m: 2.00
    output_per_1m: 8.00
  o4-mini:
    input_per_1m: 1.10
    output_per_1m: 4.40
  claude-sonnet-4-20250514:
    input_per_1m: 3.00
    output_per_1m: 15.00
  claude-3-5-haiku-20241022:
    input_per_1m: 0.80
    output_per_1m: 4.00
  claude-opus-4-20250514:
    input_per_1m: 15.00
    output_per_1m: 75.00

# 未匹配模型的默认定价（可选）
default:
  input_per_1m: 1.00
  output_per_1m: 5.00
```

**计算公式**：
```
cost = (prompt_tokens / 1_000_000) × input_per_1m
     + (completion_tokens / 1_000_000) × output_per_1m
```

支持热更新：Worker 每轮检查 pricing.yaml 的 mtime，变化时自动重载。

### 2.3 Analytics Store — 分析数据层

#### 2.3.1 analytics.db 表结构

```sql
-- ============================================================
-- conversations: 一条请求的完整结构化视图
-- ============================================================
CREATE TABLE conversations (
    id                  TEXT PRIMARY KEY,           -- 对应 raw_requests.id
    raw_seq             INTEGER NOT NULL,           -- 回溯引用

    -- LLM 语义字段
    provider            TEXT,                       -- openai / anthropic / unknown
    model               TEXT,
    request_type        TEXT,                       -- chat / completion / embedding

    -- 提示词
    system_prompt       TEXT,                       -- system message 全文
    user_prompt         TEXT,                       -- 最后一条 user message
    messages_count      INTEGER,                    -- 对话轮数
    has_tools           INTEGER DEFAULT 0,
    tools_list          TEXT,                       -- JSON: tool names
    temperature         REAL,
    max_tokens          INTEGER,

    -- 响应
    assistant_response  TEXT,                       -- 回复内容（截断保存，默认 max 10KB）
    finish_reason       TEXT,

    -- Token & 成本
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    total_tokens        INTEGER,
    cost_usd            REAL,

    -- 延迟
    duration_ms         REAL,
    time_to_first_token_ms REAL,                   -- TTFT（如可提取）

    -- 状态
    status              TEXT,                       -- success / error / timeout / rate_limited
    error_type          TEXT,
    error_message       TEXT,
    http_status         INTEGER,

    -- 分类
    template_id         TEXT,                       -- 提示词模板指纹
    tags                TEXT,                       -- JSON array

    -- 源信息
    path                TEXT,
    method              TEXT,
    client_ip           TEXT,

    -- 时间（冗余索引字段）
    timestamp           TEXT NOT NULL,
    date                TEXT NOT NULL,              -- YYYY-MM-DD
    hour                INTEGER                     -- 0-23
);

CREATE INDEX idx_conv_model ON conversations(model);
CREATE INDEX idx_conv_date ON conversations(date);
CREATE INDEX idx_conv_status ON conversations(status);
CREATE INDEX idx_conv_template ON conversations(template_id);
CREATE INDEX idx_conv_raw_seq ON conversations(raw_seq);
CREATE INDEX idx_conv_provider ON conversations(provider);
CREATE INDEX idx_conv_timestamp ON conversations(timestamp);

-- ============================================================
-- prompt_templates: 提示词模板库
-- ============================================================
CREATE TABLE prompt_templates (
    template_id         TEXT PRIMARY KEY,           -- sha256(system_prompt)[:16]
    system_prompt       TEXT,                       -- 模板原文
    first_seen          TEXT,
    last_seen           TEXT,
    usage_count         INTEGER DEFAULT 0,
    avg_prompt_tokens   REAL,
    avg_completion_tokens REAL,
    avg_cost_usd        REAL,
    models_used         TEXT                        -- JSON: distinct models
);

-- ============================================================
-- daily_stats: 按日聚合统计（Worker 定期刷新）
-- ============================================================
CREATE TABLE daily_stats (
    date                TEXT NOT NULL,
    model               TEXT NOT NULL DEFAULT '',
    request_type        TEXT NOT NULL DEFAULT '',
    provider            TEXT NOT NULL DEFAULT '',

    total_requests      INTEGER DEFAULT 0,
    success_count       INTEGER DEFAULT 0,
    error_count         INTEGER DEFAULT 0,

    total_prompt_tokens    INTEGER DEFAULT 0,
    total_completion_tokens INTEGER DEFAULT 0,
    total_cost_usd      REAL DEFAULT 0,

    avg_duration_ms     REAL,
    p50_duration_ms     REAL,
    p95_duration_ms     REAL,
    p99_duration_ms     REAL,
    max_duration_ms     REAL,

    PRIMARY KEY (date, model, request_type, provider)
);

-- ============================================================
-- watermark: 游标管理
-- ============================================================
CREATE TABLE watermark (
    key                 TEXT PRIMARY KEY,           -- 'main' / extractor 名
    last_seq            INTEGER NOT NULL DEFAULT 0,
    last_run_at         TEXT NOT NULL,
    records_processed   INTEGER DEFAULT 0
);
```

### 2.4 Query API — 对外服务

#### 2.4.1 项目结构

```
api/
├── __init__.py
├── app.py                   ← FastAPI 应用工厂
├── dependencies.py          ← DB 连接等依赖
├── routers/
│   ├── __init__.py
│   ├── overview.py          ← GET /api/overview
│   ├── conversations.py     ← GET/POST /api/conversations
│   ├── costs.py             ← GET /api/costs/*
│   ├── latency.py           ← GET /api/latency/*
│   ├── prompts.py           ← GET /api/prompts/*
│   ├── models.py            ← GET /api/models/*
│   ├── errors.py            ← GET /api/errors/*
│   └── admin.py             ← POST /api/admin/analyzer/*
└── static/                  ← Dashboard 前端静态文件
    ├── index.html
    ├── app.js
    └── style.css
```

#### 2.4.2 API 端点设计

**总览**

```
GET /api/overview
  → { total_requests, success_rate, total_cost_usd, avg_duration_ms,
      requests_today, cost_today, top_models: [...], trend_7d: [...] }
```

**对话检索**

```
GET /api/conversations
  ?page=1&page_size=50
  &model=gpt-4o
  &status=success|error
  &date_from=2026-04-01&date_to=2026-04-03
  &q=keyword                           ← 搜索 prompt/response 内容
  &template_id=abc123
  &sort=timestamp&order=desc
  → { items: [...], total, page, page_size }

GET /api/conversations/:id
  → { ...conversation, raw_request_body, raw_response_body }

GET /api/conversations/:id/raw
  → { request_headers, request_body, response_headers, response_body }
```

**成本分析**

```
GET /api/costs/summary
  ?date_from=&date_to=
  → { total_cost_usd, total_tokens, total_requests }

GET /api/costs/by-model
  ?date_from=&date_to=
  → [{ model, cost_usd, total_tokens, request_count }]
```

**延迟分析**

```
GET /api/latency/summary
  ?model=&date_from=&date_to=
  → { avg, p50, p95, p99, count }
```

**提示词**

```
GET /api/prompts/templates
  ?sort=usage_count&order=desc&limit=20
  → { items: [{ template_id, system_prompt_preview, usage_count, ... }] }

GET /api/prompts/templates/:id
  → { ...template, recent_conversations: [...] }
```

**模型统计**

```
GET /api/models/usage
  ?date_from=&date_to=
  → [{ model, request_count, total_tokens, cost_usd, avg_duration_ms }]
```

**错误**

```
GET /api/errors/summary
  ?date_from=&date_to=
  → { total_requests, error_count, error_rate, top_error_types }

GET /api/errors/recent
  ?limit=50
  → [{ id, timestamp, model, error_type, error_message, ... }]
```

**管理**

```
POST /api/admin/analyzer/rerun
  body: { mode: "full" | "incremental" | "range", since?: "...", until?: "..." }
  → { status: "completed", mode, processed, last_seq, since, until }

GET /api/admin/analyzer/status
  → { watermark_seq, records_processed, conversation_count, template_count }
```

#### 2.4.3 Dashboard

**技术选型**：纯静态 HTML + Vanilla JS + 轻量 CSS。不引入前端框架和构建工具。

| 页面 | 路由 | 内容 |
|------|------|------|
| Overview | `/` | 指标卡片（总请求量、成功率、总成本、P95 延迟）+ 7 天趋势折线图 + 模型分布 |
| Conversations | `/conversations` | 可搜索/过滤/分页表格，点击展开 prompt + response 详情 |
| Costs | `/costs` | 按模型/日期的成本柱状图/折线图 |
| Prompts | `/prompts` | 提示词模板列表，点击查看关联请求 |
| Errors | `/errors` | 错误请求列表，按类型分组统计 |

图表库选型：[Chart.js](https://www.chartjs.org/)（单 JS 文件引入，不需要构建）。

## 3. 数据安全设计

### 3.1 存储安全

| 层级 | 措施 |
|------|------|
| SQLite | WAL 模式 + `PRAGMA synchronous=NORMAL`；写入失败不会损坏已有数据 |
| JSONL | 每条写入后 flush；按小时分片，单文件损坏不影响其他时段 |
| Manifest | 同步追加，提供 ref → 文件映射的冗余索引 |
| 备份 | cron 每日执行 `sqlite3 raw.db ".backup ..."` |
| Volume | Docker named volume 持久化，支持外部备份 |

### 3.2 进程隔离

```
llm-proxy   → raw.db (读写)    bodies/ (读写)    ← 双写：JSONL + inline BLOB
analyzer    → raw.db (只读)    bodies/ (只读)    analytics.db (读写)   ← 优先读 inline BLOB
api         → raw.db (只读)    bodies/ (只读)    analytics.db (只读)   ← API 读取 body 时也优先 inline BLOB
```

SQLite WAL 模式天然支持一写多读的并发模型。

### 3.3 敏感数据

- `raw.db` 和 `bodies/` 包含完整原始数据（含 API Key 等），目录权限限制为 `0700`
- `analytics.db` 中 request_headers 已不再保存，API Key 不会泄露到分析层
- Dashboard/API 端口仅内网监听，不对公网暴露

## 4. 项目目录结构

```
llm-proxy/
├── app/                             ← 代理层（最小改动）
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── proxy.py
│   ├── recorder.py                  ← 增强：seq、client_ip、body 分片
│   ├── sse.py
│   └── ws.py
│
├── analyzer/                        ← 新增：分析 Worker
│   ├── __init__.py
│   ├── __main__.py
│   ├── worker.py
│   ├── body_reader.py
│   ├── extractors/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── openai_compat.py
│   │   ├── anthropic.py
│   │   └── generic.py
│   ├── cost.py
│   ├── fingerprint.py
│   ├── store.py
│   └── config.py
│
├── api/                             ← 新增：Query API + Dashboard
│   ├── __init__.py
│   ├── app.py
│   ├── dependencies.py
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── overview.py
│   │   ├── conversations.py
│   │   ├── costs.py
│   │   ├── latency.py
│   │   ├── prompts.py
│   │   ├── models.py
│   │   ├── errors.py
│   │   └── admin.py
│   └── static/
│       ├── index.html
│       ├── app.js
│       └── style.css
│
├── config.yaml                      ← 现有配置
├── pricing.yaml                     ← 新增：模型定价表
├── pyproject.toml                   ← 更新依赖
├── docker-compose.yml               ← 更新：三服务编排
├── Dockerfile                       ← 现有（代理）
├── Dockerfile.analyzer              ← 新增
├── Dockerfile.api                   ← 新增
├── tests/
│   ├── ...                          ← 现有测试
│   ├── test_analyzer/               ← 新增
│   └── test_api/                    ← 新增
└── docs/
    ├── README.md
    ├── 01-requirements.md
    ├── 02-architecture.md           ← 本文档
    ├── 03-development-plan.md
    ├── 04-deployment.md
    └── 05-operations.md
```

## 5. 技术栈

| 组件 | 技术 | 版本要求 |
|------|------|----------|
| 代理层 | Starlette + httpx + websockets | 现有 |
| 分析 Worker | Python stdlib + sqlite3 | Python ≥3.12 |
| Query API | FastAPI + uvicorn | FastAPI ≥0.110 |
| Dashboard | HTML + Vanilla JS + Chart.js | 无构建 |
| 存储 | SQLite (WAL) + JSONL | SQLite ≥3.35 |
| 容器 | Docker + Docker Compose | |
| 包管理 | uv | 现有 |
