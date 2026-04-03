# LLM Proxy Analytics — 文档索引

## 项目简介

基于已有的 LLM 透明反向代理（llm-proxy），构建流量分析系统：增强原始数据录制、异步提取分析、提供查询 API 和管理 Dashboard。

## 文档目录

| # | 文档 | 内容 | 适合谁看 |
|---|------|------|----------|
| 01 | [项目需求规格](01-requirements.md) | 功能需求、非功能需求、数据流、术语表 | 所有人 |
| 02 | [技术架构设计](02-architecture.md) | 系统架构、DB schema、Extractor 设计、API 端点、项目目录结构 | 开发者 |
| 03 | [开发测试验收计划](03-development-plan.md) | 分阶段任务清单、测试计划、验收标准、里程碑 | 开发者 |
| 04 | [部署使用指南](04-deployment.md) | Docker Compose 部署、本地开发、配置说明、集成方式、快速验证 | 运维 / 使用者 |
| 05 | [发布维护运维手册](05-operations.md) | 发布流程、备份恢复、监控告警、常见运维操作、故障排除 | 运维 |

## 文档关系

```
01-requirements.md          ← 做什么
        │
        ▼
02-architecture.md          ← 怎么设计
        │
        ▼
03-development-plan.md      ← 怎么开发、测试、验收
        │
        ├──→ 04-deployment.md       ← 怎么部署使用
        │
        └──→ 05-operations.md       ← 怎么维护运行
```

## 快速导航

- **想了解项目要做什么** → [01-requirements.md](01-requirements.md)
- **想了解系统怎么设计** → [02-architecture.md](02-architecture.md)
- **想知道开发计划和进度** → [03-development-plan.md](03-development-plan.md)
- **想部署或集成系统** → [04-deployment.md](04-deployment.md)
- **想运维或排查问题** → [05-operations.md](05-operations.md)
