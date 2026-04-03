# Dashboard 改进方案

> 关联文档：[需求规格](01-requirements.md) · [技术架构](02-architecture.md)

## 1. 现状盘点

| 页面 | 现有能力 | 业内差距 |
|------|---------|---------|
| **Overview** | 4 指标卡片 + 7 天趋势 | 维度单一，缺模型分布、Token 趋势、热力图 |
| **Conversations** | 列表筛选 + 详情面板 + 简单优化提示 | 无 Trace 链路、无评分、无标签、无对比 |
| **Costs** | 总成本 + 30 天趋势 + 按模型汇总 | 缺按 provider/应用维度、无预算告警、无 Token 明细 |
| **Prompts** | 模板列表(template_id + 使用次数) | 缺版本管理、A/B 对比、无模板详情页 |
| **Errors** | 错误摘要 + 最近错误列表 | 缺错误趋势图、溯源链路、无告警 |
| *(缺失)* | 后端已有 latency/models API **未接入前端** | 缺延迟分析页、模型对比页 |

对标平台：LangSmith、Langfuse、Helicone、PromptLayer、Braintrust。

---

## 2. 改进方案

### Phase 1 — P0：补齐基础 & 利用已有 API

#### 1.1 Overview 页升级
- **新增指标卡片**：总 Token 用量、活跃模型数、今日请求量、今日成本
- **模型分布饼图**：当前哪些模型在用、占比多少（后端 `models/usage` API 已有）
- **Token 使用趋势线**：与请求量/成本叠加在同一趋势图
- **时间范围选择器**：支持 「今天 / 7天 / 30天 / 自定义」全局切换

#### 1.2 延迟分析页（新增 `latency.html`）
- 后端 `latency/summary` 和 `latency/by-model` API **已实现但前端未接入**
- P50/P95/P99 分位值卡片
- 按模型延迟对比柱状图
- 延迟趋势线（需后端补一个 `/latency/daily` 接口）

#### 1.3 模型分析页（新增 `models.html`）
- 后端 `models/usage` 和 `models/list` API **已实现但前端未接入**
- 模型使用量排行榜
- 模型 × 成功率 × 延迟 × 成本 多维对比表
- 模型使用量趋势（按日折线图）

#### 1.4 Conversations 详情增强
- **消息时间线视图**：将 `messages[]` 数组渲染为角色分色的聊天气泡（system/user/assistant/tool）
- **Token 用量环形图**：增加可视化 prompt vs completion 占比
- **一键导航到同模板对话**

---

### Phase 2 — P1：提示词分析核心能力

#### 2.1 提示词模板详情页
- 完整 System Prompt 内容查看
- 使用该模板的所有对话列表
- 成本/延迟/Token 汇总指标
- 使用趋势图（按天）

#### 2.2 提示词版本差异对比
- 后端：模板相似度检测（编辑距离），自动关联「疑似同源模板」
- 前端：Diff 对比视图，左右对照两个版本的 system prompt 差异
- 效果对比表：A vs B 的成功率、延迟、成本、Token 用量

#### 2.3 提示词质量评分体系
- **自动评分维度**（基于现有数据即可计算）：
  - 成本效率分：cost_per_completion_token
  - 稳定性分：同模板对话的成功率
  - 效率分：completion/prompt token 比
  - 截断率：finish_reason=length 的占比
- **人工评分**（需后端新增字段）：
  - 对话详情页增加 👍👎 或 1-5 星评分按钮
  - 后端 `conversations` 表新增 `rating INTEGER`、`rating_comment TEXT` 字段
  - Prompts 页展示平均评分

---

### Phase 3 — P2：可观测性与运营能力

#### 3.1 Trace 链路追踪
- `conversations` 表增加 `trace_id TEXT`、`parent_id TEXT`、`span_name TEXT`
- 录制层：从请求 headers 中提取 `x-trace-id`
- 前端：树形/瀑布流视图展示同一 Trace 下的调用链

#### 3.2 标签 & 注解系统
- `conversations` 表增加 `tags TEXT` (JSON 字段)
- 前端：打标、按标签过滤、批量打标

#### 3.3 错误分析增强
- 错误趋势图（后端补 `/errors/daily` 接口）
- 错误类型分布饼图
- 错误详情溯源

#### 3.4 数据集导出
- `/conversations/export` 接口
- 导出格式：JSONL（OpenAI fine-tuning 格式）、CSV
- 按筛选条件导出

---

### Phase 4 — P3：高级功能

#### 4.1 Prompt Playground（提示词试验场）
#### 4.2 实时监控 & 告警
#### 4.3 用户/应用维度聚合
#### 4.4 LLM-as-Judge 自动评估

---

## 3. 架构改动汇总

| 层 | 改动 |
|----|------|
| **analytics.db** | `conversations` 新增字段: `trace_id`, `parent_id`, `span_name`, `rating`, `tags`, `auto_score` |
| **后端 API** | 新增: `/latency/daily`, `/errors/daily`, `/conversations/export`, `/conversations/{id}/rating`, Trace 相关接口 |
| **前端页面** | 新增: `latency.html`, `models.html`; 大幅增强: `index.html`, `prompts.html`, `conversations.html` |
| **录制层** | 从 headers 提取 `x-trace-id`, `x-user-id`, `x-app-id` 写入 raw.db |

## 4. 实施路径

```
Phase 1 (近期)  → P0: 补全 latency/models 页面, Overview 升级, Conversations 聊天视图
Phase 2 (中期)  → P1: 提示词模板详情页, 版本 Diff 对比, 质量评分体系
Phase 3 (后期)  → P2: Trace 链路, 标签系统, 数据集导出, 错误分析增强
Phase 4 (远期)  → P3: Playground, 实时监控, LLM-as-Judge
```
