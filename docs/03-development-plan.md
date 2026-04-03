# LLM Proxy Analytics — 开发测试验收计划

> 关联文档：[需求规格](01-requirements.md) · [技术架构](02-architecture.md) · [部署指南](04-deployment.md) · [运维手册](05-operations.md)

## 1. 阶段总览

```
Phase 0 ──→ Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 4
基础准备     录制增强     分析Worker    查询API      Dashboard
                                    + Dashboard
```

| 阶段 | 名称 | 核心交付 | 依赖 |
|------|------|----------|------|
| P0 | 基础准备 | 项目结构、依赖、配置、DB migration | 无 |
| P1 | 录制增强 | recorder.py 改造，raw.db 新表结构，body 分片 | P0 |
| P2 | 分析 Worker | analyzer 模块，OpenAI extractor，成本计算，指纹 | P0 |
| P3 | 查询 API + Dashboard | FastAPI 端点，静态前端 | P2 |
| P4 | 扩展 & 加固 | 更多 extractor，WS 分析，备份，性能优化 | P3 |

> P1 和 P2 可并行开发（P2 可基于手工构造的测试数据开发）。

---

## 2. Phase 0：基础准备

### 2.1 任务清单

| # | 任务 | 交付物 |
|---|------|--------|
| 0.1 | 创建 `analyzer/` 和 `api/` 包目录结构 | 空包骨架 |
| 0.2 | 更新 `pyproject.toml` 添加新依赖（fastapi, chart.js CDN） | pyproject.toml |
| 0.3 | 创建 `pricing.yaml` 模板 | pricing.yaml |
| 0.4 | 编写 DB schema migration 脚本 | scripts/migrate.py |
| 0.5 | 新增 `Dockerfile.analyzer` 和 `Dockerfile.api` | Dockerfile.* |
| 0.6 | 更新 `docker-compose.yml` 三服务编排 | docker-compose.yml |

### 2.2 验收标准

- [ ] `uv sync` 成功安装所有依赖
- [ ] 三个服务（proxy, analyzer, api）可独立启动不报错
- [ ] Migration 脚本可对空库或现有 proxy.db 执行升级

---

## 3. Phase 1：录制增强

### 3.1 任务清单

| # | 任务 | 改动文件 | 说明 |
|---|------|----------|------|
| 1.1 | 重构 Recorder：raw.db 替代 proxy.db | `app/recorder.py` | 表名改为 `raw_requests` 等 |
| 1.2 | 实现 SeqGenerator | `app/recorder.py` | 线程安全自增序号 |
| 1.3 | 记录 client_ip/port | `app/proxy.py`, `app/ws.py` | 从 request.client 提取 |
| 1.4 | 记录 body_size | `app/recorder.py` | 写 body 时顺带记录字节数 |
| 1.5 | 记录 upstream_url | `app/proxy.py` | 传入实际转发 URL |
| 1.6 | JSONL 按小时分片 | `app/recorder.py` | 新增文件轮转逻辑 |
| 1.7 | Manifest 索引写入 | `app/recorder.py` | 每次写 body 追加 manifest |
| 1.8 | 移除 recorder 中的 model 提取 | `app/recorder.py` | 录制层不再做 JSON 解析 |
| 1.9 | 兼容性：旧 proxy.db 数据迁移 | `scripts/migrate.py` | 可选，手动执行 |

### 3.2 测试计划

| 测试类型 | 内容 | 文件 |
|----------|------|------|
| 单元测试 | SeqGenerator 线程安全、单调递增 | `tests/test_recorder.py` |
| 单元测试 | JSONL 分片轮转（跨小时写入） | `tests/test_recorder.py` |
| 单元测试 | Manifest 写入和读取一致性 | `tests/test_recorder.py` |
| 集成测试 | 代理完整请求 → 检查 raw.db 各字段正确 | `tests/test_http_proxy.py` |
| 集成测试 | SSE 流式请求 → body_size 和 body_ref 正确 | `tests/test_http_proxy.py` |
| 集成测试 | WebSocket 请求 → ws 表记录完整 | `tests/test_ws_proxy.py` |
| 性能测试 | 1000 次连续写入，额外延迟 < 1ms/次 | 手动验证 |

### 3.3 验收标准

- [ ] 所有现有测试通过（无回归）
- [ ] raw.db 包含 seq、client_ip、body_size、upstream_url 字段
- [ ] Body JSONL 按小时自动分文件
- [ ] Manifest 文件与 JSONL 文件一致
- [ ] Recorder 不再做任何 JSON 解析
- [ ] 性能：单次录制额外开销 < 1ms

---

## 4. Phase 2：分析 Worker

### 4.1 任务清单

| # | 任务 | 交付物 |
|---|------|--------|
| 2.1 | 实现 Extractor 基类和接口 | `analyzer/extractors/base.py` |
| 2.2 | 实现 OpenAI 兼容 Extractor | `analyzer/extractors/openai_compat.py` |
| 2.3 | 实现 Generic Extractor（兜底） | `analyzer/extractors/generic.py` |
| 2.4 | 实现 BodyReader（从分片 JSONL 读取） | `analyzer/body_reader.py` |
| 2.5 | 实现 CostCalculator（读 pricing.yaml） | `analyzer/cost.py` |
| 2.6 | 实现 Fingerprinter（提示词指纹） | `analyzer/fingerprint.py` |
| 2.7 | 实现 AnalyticsStore（写 analytics.db） | `analyzer/store.py` |
| 2.8 | 实现 Worker 主循环 + 游标管理 | `analyzer/worker.py` |
| 2.9 | 实现 CLI 入口 + 配置 | `analyzer/__main__.py`, `analyzer/config.py` |
| 2.10 | 实现每日聚合统计刷新 | `analyzer/worker.py` |

### 4.2 测试计划

| 测试类型 | 内容 | 文件 |
|----------|------|------|
| 单元测试 | OpenAI Extractor：解析 chat completion 请求/响应 | `tests/test_analyzer/test_openai_extractor.py` |
| 单元测试 | OpenAI Extractor：解析 SSE 流式响应（多 chunk 拼接）| `tests/test_analyzer/test_openai_extractor.py` |
| 单元测试 | OpenAI Extractor：解析 embedding 请求 | `tests/test_analyzer/test_openai_extractor.py` |
| 单元测试 | Generic Extractor：只提取 HTTP 级别信息 | `tests/test_analyzer/test_generic_extractor.py` |
| 单元测试 | CostCalculator：已知模型价格计算 | `tests/test_analyzer/test_cost.py` |
| 单元测试 | CostCalculator：未知模型用默认价格 | `tests/test_analyzer/test_cost.py` |
| 单元测试 | CostCalculator：pricing.yaml 热更新 | `tests/test_analyzer/test_cost.py` |
| 单元测试 | Fingerprinter：相同 prompt 生成相同指纹 | `tests/test_analyzer/test_fingerprint.py` |
| 单元测试 | BodyReader：跨分片文件读取 | `tests/test_analyzer/test_body_reader.py` |
| 集成测试 | Worker 增量模式：处理 N 条新记录 → watermark 更新 | `tests/test_analyzer/test_worker.py` |
| 集成测试 | Worker 全量重跑：清空 → 重建 → 数据一致 | `tests/test_analyzer/test_worker.py` |
| 集成测试 | Worker 范围重跑：只处理指定时间段 | `tests/test_analyzer/test_worker.py` |
| 集成测试 | Worker 断点续跑：模拟中断 → 重启 → 不重复不遗漏 | `tests/test_analyzer/test_worker.py` |

### 4.3 验收标准

- [ ] 增量模式：新写入 raw.db 的记录在 5s 内出现在 analytics.db
- [ ] 全量重跑：清空 analytics.db → 重建 → 与增量结果一致
- [ ] 范围重跑：只处理指定时间段，不影响其他数据
- [ ] OpenAI chat completion 请求正确提取所有字段
- [ ] SSE 流式响应的 usage 信息正确提取（来自最后一个 chunk）
- [ ] 成本计算结果与手动计算一致
- [ ] 提示词指纹稳定（相同 prompt → 相同 fingerprint）
- [ ] Worker 崩溃重启后从 watermark 断点续跑
- [ ] analytics.db 可删除并从 raw 数据完全重建

---

## 5. Phase 3：查询 API + Dashboard

### 5.1 任务清单

| # | 任务 | 交付物 |
|---|------|--------|
| 3.1 | FastAPI 应用骨架 + 依赖注入 | `api/app.py`, `api/dependencies.py` |
| 3.2 | Overview API | `api/routers/overview.py` |
| 3.3 | Conversations API（列表 + 详情 + 原始回溯） | `api/routers/conversations.py` |
| 3.4 | Costs API | `api/routers/costs.py` |
| 3.5 | Latency API | `api/routers/latency.py` |
| 3.6 | Prompts API | `api/routers/prompts.py` |
| 3.7 | Models API | `api/routers/models.py` |
| 3.8 | Errors API | `api/routers/errors.py` |
| 3.9 | Admin API（触发重分析 + 状态查询） | `api/routers/admin.py` |
| 3.10 | Dashboard - Overview 页面 | `api/static/` |
| 3.11 | Dashboard - Conversations 页面 | `api/static/` |
| 3.12 | Dashboard - Costs 页面 | `api/static/` |
| 3.13 | Dashboard - Prompts 页面 | `api/static/` |
| 3.14 | Dashboard - Errors 页面 | `api/static/` |

### 5.2 测试计划

| 测试类型 | 内容 | 文件 |
|----------|------|------|
| 单元测试 | 每个 API 端点的正常返回结构 | `tests/test_api/test_*.py` |
| 单元测试 | 分页参数边界 | `tests/test_api/test_conversations.py` |
| 单元测试 | 过滤条件组合 | `tests/test_api/test_conversations.py` |
| 单元测试 | 空数据库场景 | `tests/test_api/test_overview.py` |
| 集成测试 | API ↔ analytics.db 端到端 | `tests/test_api/test_integration.py` |
| 集成测试 | 原始回溯：API → analytics → raw.db → body | `tests/test_api/test_conversations.py` |
| 手动测试 | Dashboard 各页面功能验证 | 浏览器手动 |

### 5.3 验收标准

- [ ] 所有 API 端点返回符合约定的 JSON 结构
- [ ] 列表接口支持分页（page/page_size）
- [ ] 对话搜索支持 model/status/date/关键词过滤
- [ ] 成本汇总数据准确（抽查对比 analytics.db 手动查询）
- [ ] 延迟百分位数计算正确
- [ ] 原始数据回溯路径通畅
- [ ] Dashboard Overview 页面正常展示指标和图表
- [ ] Dashboard Conversations 页面可搜索、过滤、查看详情
- [ ] Dashboard 无需任何构建工具，纯静态文件

---

## 6. Phase 4：扩展 & 加固

### 6.1 任务清单

| # | 任务 | 优先级 |
|---|------|--------|
| 4.1 | Anthropic Extractor | P2 |
| 4.2 | WebSocket Realtime API Extractor | P2 |
| 4.3 | 自动备份脚本（cron） | P1 |
| 4.4 | 数据归档/清理策略 | P2 |
| 4.5 | API 认证（Token-based） | P2 |
| 4.6 | 性能优化：批量写入、连接池 | P2 |
| 4.7 | Grafana JSON API 数据源适配 | P3 |
| 4.8 | 更多 Dashboard 图表 | P3 |

### 6.2 验收标准

- [ ] Anthropic API 请求正确解析
- [ ] 备份脚本可自动执行，备份文件可恢复
- [ ] API 认证正常工作，无 Token 请求被拒绝

---

## 7. 测试策略

### 7.1 测试分层

```
              ┌─────────────────┐
              │   E2E 测试       │  ← 手动 / 可选自动化
              │ (全链路验证)      │
              ├─────────────────┤
              │   集成测试        │  ← pytest，真实 SQLite
              │ (组件间交互)      │
              ├─────────────────┤
              │   单元测试        │  ← pytest，mock 外部依赖
              │ (函数/类级别)     │
              └─────────────────┘
```

### 7.2 测试数据准备

为测试准备标准化的 fixture 数据：

```python
# tests/fixtures/
├── openai_chat_request.json         # 标准 chat completion 请求
├── openai_chat_response.json        # 标准同步响应
├── openai_chat_stream_chunks.txt    # SSE 流式响应（多 chunk）
├── openai_embedding_request.json    # embedding 请求
├── anthropic_message_request.json   # Anthropic message 请求
├── error_response_429.json          # 限流响应
├── error_response_500.json          # 内部错误响应
└── pricing_test.yaml                # 测试用定价表
```

### 7.3 运行测试

```bash
# 全部测试
uv run pytest

# 按模块
uv run pytest tests/test_analyzer/
uv run pytest tests/test_api/

# 带覆盖率
uv run pytest --cov=app --cov=analyzer --cov=api
```

### 7.4 兼容性测试

| 场景 | 验证方式 |
|------|----------|
| 现有 proxy.db 迁移到 raw.db | migration 脚本 + 数据对比 |
| 无历史数据的全新启动 | 三服务全新启动测试 |
| 大批量数据 (10k+ 记录) | 性能基准测试 |
| 代理与分析同时写读 raw.db | 并发压力测试 |

---

## 8. 代码质量要求

| 维度 | 要求 |
|------|------|
| 类型标注 | 所有公共函数必须有类型标注 |
| 错误处理 | Worker/API 中捕获异常并记录日志，不静默失败 |
| 日志 | 统一使用 `logging`，模块级 logger |
| 测试覆盖 | 核心逻辑（extractor、cost、worker）≥ 80% |
| SQL 安全 | 全部使用参数化查询，禁止字符串拼接 SQL |

---

## 9. 里程碑检查点

| 检查点 | 条件 | 阻塞 |
|--------|------|------|
| M0 完成 | 项目骨架可构建，三服务可独立启动 | 进入 P1/P2 |
| M1 完成 | 录制增强通过所有测试，现有测试无回归 | P2 可用真实数据 |
| M2 完成 | Worker 增量模式正常运行，analytics.db 有正确数据 | 进入 P3 |
| M3 完成 | API 和 Dashboard 可用，全链路数据流通 | 可交付使用 |
| M4 完成 | 扩展功能就绪，备份策略到位 | 生产加固 |
