# LLM Proxy Analytics — 发布维护运维手册

> 关联文档：[技术架构](02-architecture.md) · [部署指南](04-deployment.md)

## 1. 发布流程

### 1.1 版本管理

使用语义化版本（SemVer）：`MAJOR.MINOR.PATCH`

| 类型 | 适用场景 | 示例 |
|------|----------|------|
| PATCH | Bug 修复，安全补丁 | 0.2.0 → 0.2.1 |
| MINOR | 新增 Extractor、API 端点、Dashboard 页面 | 0.2.1 → 0.3.0 |
| MAJOR | DB schema 不兼容变更、API breaking change | 0.3.0 → 1.0.0 |

版本号在 `pyproject.toml` 中统一管理。

### 1.2 发布检查清单

```
发布前：
  [ ] 所有测试通过：uv run pytest
  [ ] 无类型错误（如使用 mypy/pyright）
  [ ] 更新 pyproject.toml 版本号
  [ ] 如有 DB schema 变更，编写 migration 脚本
  [ ] 更新 CHANGELOG（如维护）

构建：
  [ ] docker compose build
  [ ] 验证三个镜像构建成功

部署：
  [ ] 备份 raw.db：sqlite3 raw.db ".backup raw-pre-release.db.bak"
  [ ] docker compose down
  [ ] 执行 migration 脚本（如有）
  [ ] docker compose up -d
  [ ] 验证三个服务健康

验证：
  [ ] curl http://localhost:9090/health → ok
  [ ] Dashboard 可访问
  [ ] 发一条测试请求，5s 后在 API 中可查到
```

### 1.3 回滚流程

```bash
# 1. 停止服务
docker compose down

# 2. 回退镜像（如未清除旧镜像）
docker compose up -d --no-build  # 使用上次构建的镜像

# 3. 如果 DB schema 有变更，恢复备份
docker compose exec llm-proxy sh -c \
  "cp /data/backup/raw-pre-release.db.bak /data/logs/raw.db"

# 4. 重启
docker compose up -d
```

## 2. 数据管理

### 2.1 备份策略

#### 自动备份（推荐）

创建 cron 任务，每日备份 raw.db：

```bash
# /etc/cron.d/llm-proxy-backup
0 2 * * * root docker compose -f /path/to/docker-compose.yml \
  exec -T llm-proxy sh -c \
  'sqlite3 /data/logs/raw.db ".backup /data/backup/raw-$(date +\%Y-\%m-\%d).db.bak"' \
  2>&1 | logger -t llm-proxy-backup
```

**备份保留策略**：

```bash
# 清理 30 天前的备份
find /data/backup -name "raw-*.db.bak" -mtime +30 -delete
```

#### 手动备份

```bash
# SQLite 在线备份（不影响服务）
docker compose exec llm-proxy \
  sqlite3 /data/logs/raw.db ".backup /data/backup/raw-manual.db.bak"

# 备份 body 文件（可选，占空间大）
docker compose exec llm-proxy \
  tar czf /data/backup/bodies-$(date +%Y-%m-%d).tar.gz /data/logs/bodies/
```

#### analytics.db 不需要备份

analytics.db 完全由 raw 数据派生，可随时通过全量重跑重建：

```bash
docker compose exec analyzer python -m analyzer --mode=full
```

### 2.2 数据归档

当数据量增长时，可归档旧的 body 文件：

```bash
# 归档 30 天前的 JSONL 分片
find /data/logs/bodies -name "*.jsonl" -not -name "manifest.jsonl" \
  -mtime +30 -exec gzip {} \;

# 如需恢复分析，先解压再重跑
gunzip /data/logs/bodies/2026-03-*.jsonl.gz
docker compose exec analyzer python -m analyzer --mode=range \
  --since=2026-03-01 --until=2026-03-31
```

### 2.3 数据清理

```bash
# 清理 raw.db 中超过 N 天的记录（谨慎操作）
docker compose exec llm-proxy sqlite3 /data/logs/raw.db \
  "DELETE FROM raw_requests WHERE timestamp < datetime('now', '-90 days');"

# 清理对应的 body 文件
find /data/logs/bodies -name "*.jsonl" -not -name "manifest.jsonl" \
  -mtime +90 -delete

# 重建 analytics
docker compose exec analyzer python -m analyzer --mode=full
```

## 3. 监控

### 3.1 健康检查

```bash
# 代理服务
curl -s http://localhost:9090/health
# → {"status": "ok"}

# API 服务
curl -s http://localhost:9091/api/admin/analyzer/status
# → {"watermark_seq": 12345, "records_processed": 12345, "conversation_count": 12345, "template_count": 120}

# Docker 层面
docker compose ps
```

### 3.2 关键指标

| 指标 | 获取方式 | 告警阈值 |
|------|----------|----------|
| 代理响应延迟 | `duration_ms` in raw.db | P95 > 10s |
| 代理错误率 | `status_code >= 500` 占比 | > 5% |
| 分析延迟 (lag) | `MAX(seq) in raw` - `last_seq in watermark` | > 1000 条 |
| 磁盘使用 | `du -sh /data/logs/ /data/analytics/` | > 80% 可用空间 |
| 容器状态 | `docker compose ps` | 任何服务非 running |

### 3.3 日志查看

```bash
# 实时日志
docker compose logs -f --tail=100

# 按服务查看
docker compose logs -f llm-proxy
docker compose logs -f analyzer
docker compose logs -f api

# 搜索错误
docker compose logs analyzer 2>&1 | grep -i error
```

## 4. 常见运维操作

### 4.1 重启单个服务

```bash
# 重启代理（analyzer 和 api 不受影响）
docker compose restart llm-proxy

# 重启分析 Worker
docker compose restart analyzer

# 重启 API
docker compose restart api
```

### 4.2 更新模型定价

```bash
# 1. 编辑定价文件
vim pricing.yaml

# 2. 无需重启！Worker 会在下一轮循环自动检测 mtime 变化并重载
# 3. 如需重新计算历史成本
docker compose exec analyzer python -m analyzer --mode=full
```

### 4.3 添加新的 Extractor

```bash
# 1. 开发新 extractor（如 anthropic.py）
# 2. 在 worker.py 的 extractors 列表中注册
# 3. 重建并重启 analyzer
docker compose build analyzer
docker compose restart analyzer

# 4. 全量重跑以处理历史数据
docker compose exec analyzer python -m analyzer --mode=full
```

### 4.4 重建分析数据

```bash
# 完全重建（删除 analytics.db，从头处理）
docker compose exec analyzer python -m analyzer --mode=full

# 按时间范围补跑
docker compose exec analyzer python -m analyzer \
  --mode=range --since=2026-04-01 --until=2026-04-03
```

### 4.5 查看原始数据

```bash
# 进入代理容器查询 raw.db
docker compose exec llm-proxy sqlite3 /data/logs/raw.db

# 最近 10 条请求
SELECT seq, id, method, path, status_code, duration_ms
FROM raw_requests ORDER BY seq DESC LIMIT 10;

# 按状态码统计
SELECT status_code, COUNT(*) FROM raw_requests GROUP BY status_code;

# 查看分析进度
docker compose exec api sqlite3 /data/analytics/analytics.db \
  "SELECT * FROM watermark;"
```

### 4.6 磁盘空间管理

```bash
# 查看各部分占用
docker compose exec llm-proxy sh -c \
  "du -sh /data/logs/raw.db /data/logs/bodies/"

docker compose exec api sh -c \
  "du -sh /data/analytics/analytics.db"

# body 文件通常是最大的消费者
# 查看各月占用
docker compose exec llm-proxy sh -c \
  "ls -lhS /data/logs/bodies/*.jsonl | head -20"
```

## 5. 故障排除

### 5.1 代理无法连接上游

```
症状：客户端收到 502 Bad Gateway
检查：
  1. docker compose logs llm-proxy | grep "connect error"
  2. 验证上游地址：curl http://upstream-host:8080/health
  3. 检查网络：docker compose exec llm-proxy ping upstream-host
```

### 5.2 分析数据不更新

```
症状：Dashboard 数据停止更新
检查：
  1. docker compose ps analyzer  → 确认服务 running
  2. docker compose logs analyzer → 查看是否有错误
  3. 检查 lag：
     raw_max=$(docker compose exec llm-proxy sqlite3 /data/logs/raw.db \
       "SELECT MAX(seq) FROM raw_requests;")
     wm=$(docker compose exec api sqlite3 /data/analytics/analytics.db \
       "SELECT last_seq FROM watermark WHERE key='main';")
     echo "Lag: $((raw_max - wm))"
  4. 如果 Worker 卡住，重启：docker compose restart analyzer
```

### 5.3 磁盘空间不足

```
症状：写入失败，服务异常
紧急处理：
  1. 归档旧 body 文件：见 4.6 节
  2. 清理旧备份：find /data/backup -mtime +7 -delete
  3. 如果 analytics.db 过大，删除后重建（它是可派生的）
```

### 5.4 SQLite 锁冲突

```
症状：日志出现 "database is locked"
原因：通常是手动 sqlite3 CLI 长时间持有写锁
处理：
  1. 确认没有手动的 sqlite3 会话在运行
  2. 确认 WAL 模式启用：
     sqlite3 raw.db "PRAGMA journal_mode;"  → 应返回 "wal"
  3. 如果持续出现，检查是否有多个 proxy 实例写同一个 DB
```

## 6. 安全注意事项

| 事项 | 措施 |
|------|------|
| API/Dashboard 端口 | 仅绑定内网地址，不对公网暴露 |
| 原始数据含敏感信息 | /data/logs 目录权限限制；Docker volume 默认隔离 |
| API 认证（Phase 4） | 上线后建议加 Token 认证或反代鉴权 |
| SQLite 文件 | 不要通过网络共享 SQLite 文件（NFS 不可靠） |
