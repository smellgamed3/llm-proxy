# LLM Proxy Analytics — 项目需求规格

## 1. 项目背景

已有一个运行中的 LLM 透明反向代理（`llm-proxy`），基于 Starlette + httpx 实现，支持 HTTP 和 WebSocket 协议。当前已具备：

- 透明反向代理（HTTP + WebSocket 双协议）
- 基于 SQLite + JSONL 的请求/响应录制
- 路径过滤规则（include/exclude）
- SSE 流式响应的完整捕获

**现状差距**：有原始数据存储，但缺少结构化提取、分析、查询和可视化能力。

## 2. 项目目标

在**不影响代理服务性能和稳定性**的前提下，构建一套完整的 LLM 流量分析系统：

1. **增强录制**：扩展原始数据采集的完整性，为未来未知分析需求留足余地
2. **异步分析**：独立 Worker 进程提取、计算结构化指标
3. **查询服务**：RESTful API 供内外部系统检索分析数据
4. **管理界面**：Dashboard 供人工管理调试

> 当前 `0.2.x` 版本交付范围聚焦 P0-P3 主链路：录制增强、HTTP/Anthropic 分析、查询 API、静态 Dashboard。
> WebSocket Realtime 语义解析与自动备份属于后续扩展项，保留在后续阶段继续交付。

## 3. 功能需求

### 3.1 录制层增强（FR-REC）

| ID | 需求 | 优先级 |
|----|------|--------|
| FR-REC-01 | 录制层零侵入：不做任何 JSON 解析或业务逻辑，只存原始数据 | P0 |
| FR-REC-02 | 新增单调递增序号（seq），供增量分析游标使用 | P0 |
| FR-REC-03 | 记录客户端 IP/Port 信息 | P1 |
| FR-REC-04 | 记录请求/响应 body 的字节大小 | P1 |
| FR-REC-05 | 记录实际转发的完整上游 URL | P1 |
| FR-REC-06 | Body JSONL 按小时分片存储，避免单文件过大 | P0 |
| FR-REC-07 | 维护 body manifest 索引文件，确保 ref → 文件映射不丢失 | P0 |
| FR-REC-08 | WebSocket 消息记录 data_size 字段 | P1 |
| FR-REC-09 | 原始数据库重命名为 raw.db（与分析库分离） | P0 |

### 3.2 分析 Worker（FR-ANA）

| ID | 需求 | 优先级 |
|----|------|--------|
| FR-ANA-01 | 独立进程运行，与代理进程完全解耦 | P0 |
| FR-ANA-02 | 支持三种运行模式：增量（默认）、全量重跑、时间范围重跑 | P0 |
| FR-ANA-03 | 基于 seq 游标（watermark）实现增量分析，断点续跑 | P0 |
| FR-ANA-04 | 提取 LLM 语义字段：model、messages、tools、temperature 等 | P0 |
| FR-ANA-05 | 提取响应字段：finish_reason、usage（token 计数） | P0 |
| FR-ANA-06 | 基于模型定价表计算请求成本 | P0 |
| FR-ANA-07 | 生成提示词指纹（system prompt hash），相同模板归类 | P1 |
| FR-ANA-08 | 分类请求状态：success / error / timeout / rate_limited | P0 |
| FR-ANA-09 | 支持 OpenAI 兼容 API 格式解析 | P0 |
| FR-ANA-10 | 支持 Anthropic API 格式解析 | P2 |
| FR-ANA-11 | 支持 WebSocket (Realtime API) 消息解析 | P2 |
| FR-ANA-12 | 聚合生成按日/模型/类型维度的统计数据 | P1 |
| FR-ANA-13 | Extractor 插件化，方便扩展新的解析格式 | P1 |
| FR-ANA-14 | 分析数据写入独立的 analytics.db，与 raw.db 物理隔离 | P0 |

### 3.3 查询 API（FR-API）

| ID | 需求 | 优先级 |
|----|------|--------|
| FR-API-01 | 提供 RESTful JSON API | P0 |
| FR-API-02 | 总览接口：请求量、成功率、总成本、平均延迟 | P0 |
| FR-API-03 | 对话列表：支持按 model/status/date/关键词 过滤和分页 | P0 |
| FR-API-04 | 对话详情：完整 prompt + response 内容 | P0 |
| FR-API-05 | 原始数据回溯：从 analytics 回溯到 raw body | P1 |
| FR-API-06 | 成本分析：按日/模型/provider 汇总 | P0 |
| FR-API-07 | 延迟分析：P50/P95/P99 分布 | P1 |
| FR-API-08 | 提示词模板列表及频率统计 | P1 |
| FR-API-09 | 错误列表及分类 | P0 |
| FR-API-10 | 模型维度统计 | P1 |
| FR-API-11 | 触发重分析（全量/增量） | P1 |

### 3.4 Dashboard（FR-UI）

| ID | 需求 | 优先级 |
|----|------|--------|
| FR-UI-01 | 总览页：关键指标卡片 + 趋势图 | P0 |
| FR-UI-02 | 对话列表页：可搜索/过滤/排序 | P0 |
| FR-UI-03 | 对话详情：展开查看 prompt 和 response | P0 |
| FR-UI-04 | 成本分析页：按维度可视化 | P1 |
| FR-UI-05 | 提示词模板浏览 | P1 |
| FR-UI-06 | 错误日志查看 | P1 |
| FR-UI-07 | 纯静态前端，不需要 Node 构建 | P0 |

## 4. 非功能需求

### 4.1 性能

| ID | 需求 |
|----|------|
| NFR-PERF-01 | 录制层额外开销 < 1ms/请求（不做 JSON 解析） |
| NFR-PERF-02 | 代理核心路径不因分析系统故障而受影响 |
| NFR-PERF-03 | 分析 Worker 处理速率 ≥ 100 req/s（追赶积压数据） |
| NFR-PERF-04 | API 查询响应 < 500ms（常规列表/聚合查询） |

### 4.2 可靠性

| ID | 需求 |
|----|------|
| NFR-REL-01 | 原始数据不丢失：JSONL 写入有 fsync，SQLite 使用 WAL |
| NFR-REL-02 | analytics.db 可随时删除重建（完全由 raw 派生） |
| NFR-REL-03 | Worker 崩溃重启后从 watermark 断点续跑，不重复不遗漏 |
| NFR-REL-04 | 支持定期自动备份 raw.db |

### 4.3 可维护性

| ID | 需求 |
|----|------|
| NFR-MNT-01 | Extractor 插件化：新增 LLM 格式只需加一个 extractor |
| NFR-MNT-02 | 定价表可热更新（修改 pricing.yaml 无需重启） |
| NFR-MNT-03 | 三个组件（proxy、analyzer、api）可独立部署/重启 |

### 4.4 安全性

| ID | 需求 |
|----|------|
| NFR-SEC-01 | 原始数据含 API Key 等敏感信息，log 目录权限限制 |
| NFR-SEC-02 | analytics.db 中不存储完整 API Key（脱敏处理） |
| NFR-SEC-03 | Dashboard/API 端口不对公网暴露（仅内网访问） |

## 5. 数据流概览

```
Client → [llm-proxy] → Upstream LLM
              │
              │ sync write (<1ms)
              ▼
        ┌──────────┐
        │ Raw Store │  raw.db + bodies/*.jsonl
        └────┬─────┘
             │ async poll (5s interval)
             ▼
        ┌──────────┐
        │ Analyzer  │  独立进程
        └────┬─────┘
             │ write
             ▼
        ┌──────────────┐
        │ Analytics DB  │  analytics.db
        └────┬─────────┘
             │ read-only
        ┌────┴────┐
        ▼         ▼
   Dashboard   Query API → 业务系统
```

## 6. 术语表

| 术语 | 说明 |
|------|------|
| Raw Store | 原始数据存储层，包含 raw.db 和 bodies JSONL 文件 |
| Analytics Store | 分析结果存储层，analytics.db |
| Watermark | 游标标记，记录分析 Worker 已处理到的位置 |
| Extractor | 分析提取器，负责从原始数据中提取结构化信息 |
| Template / Fingerprint | 提示词模板指纹，通过 hash system prompt 识别相同模板 |
| seq | 原始请求的单调递增序号，用于增量分析 |
