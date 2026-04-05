# Dashboard 改进与现状

> 关联文档：[需求规格](01-requirements.md) · [技术架构](02-architecture.md) · [开发测试验收计划](03-development-plan.md)

## 1. 文档目的

本文件用于记录 Dashboard 的当前能力、近期迭代内容与后续优化方向。

与 `03-development-plan.md` 的区别：
- `03` 关注全项目分阶段开发计划。
- `07` 聚焦 Dashboard 现状与可视化分析能力。

## 2. 当前已落地能力（1.3.0）

### 2.1 页面结构与导航

- 统一侧边栏布局（参考 LiteLLM 交互风格）
- 顶部全局信息栏（版本、环境、入口链接）
- 顶部支持亮色/暗色主题切换，主题状态持久化到浏览器本地
- 主内容区滚动，移动端支持侧栏抽屉与遮罩

### 2.2 已接入页面

- Overview：核心指标、趋势图、模型/Token 概览
- Conversations：列表筛选、详情弹窗、评分与标签
- Costs：成本汇总、按天趋势、按模型分布
- Latency：P50/P95/P99、趋势与分布
- Models：模型维度请求量/成功率/成本/延迟
- Errors：错误摘要、类型分布、最近错误
- Prompts：模板列表、模板详情与关联对话
- Analyzer：分析任务状态、任务历史与备份管理

### 2.3 Key / Hash 管理能力

- 首次录入原始 API key 后，前端立即转换并仅保存 hash，不再回显原始 key
- 本地 key 记录支持别名二次编辑、逐个激活/停用、全部启停
- 页面请求只携带当前激活的 hash 集合，便于 scoped 视角切换
- 激活集合包含 admin hash 时，页面自动切换为 admin 视角并展示全量管理能力

### 2.4 对话详情页分析能力

- 折叠分区：System、Tools、Skill 统计、History、User、Assistant、优化建议
- Tool 参数展开：支持结构化参数表查看
- Token 分析：
  - 输入/输出分布（含 cache read / cache write / reasoning）
  - 内容构成估算（system / tools / history / user / assistant）
- Skill 统计：
  - 工具定义数、使用数、调用次数、使用率
  - 能力分类分布（文件、搜索、终端、浏览器、Git、分析等）
  - Top 调用频率条形图、未使用工具提示
- 性能分析：tok/s、吞吐量、I/O 比、缓存命中、推理占比、成本效率
- 内容分析：消息角色分布、字符/词数、代码块检测、阅读时长、输入输出比
- 请求配置：temperature/max_tokens/top_p/penalty/tool_choice 等参数展示
- 智能优化建议：基于 token、速度、缓存、消息长度的启发式提示

#### 2.4.1 对话级“优化建议”当前规则

当前版本的“优化建议”不是由大模型二次分析生成，而是前端根据单条会话详情字段执行一组固定启发式规则后直接展示。

规则实现位置：`api/static/app.js` 中的 `promptOptimizationHints(detail)`。

输入字段主要包括：

- `system_prompt`
- `user_prompt`
- `assistant_response`
- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- `duration_ms`
- `finish_reason`
- `_extUsage.cacheRead`
- `_reqMessages.length`

当前触发规则如下：

| 条件 | 提示含义 |
|------|----------|
| `system_prompt.length > 2800` | System prompt 偏长，建议拆分固定政策与动态上下文，减少重复 token |
| `user_prompt.length < 30 && total_tokens > 1200` | 用户输入较短但总 token 偏高，可能上下文注入过多，建议摘要历史消息 |
| `prompt_tokens > 0 && completion_tokens / prompt_tokens < 0.2` | Completion/Prompt 比例偏低，可能提示词约束过强 |
| `finish_reason == "length"` | 输出被截断，建议提高 `max_tokens` 或压缩输入 |
| `assistant_response.length / user_prompt.length > 20` | 回复长度远高于用户输入，可增加“简洁回答”约束降低成本 |
| `prompt_tokens > 1000 && cacheRead == 0` | 输入 token 较多但未使用缓存，建议启用 Prompt Caching |
| `cacheRead > 0 && cacheRead / prompt_tokens > 0.8` | 缓存命中率优秀，说明 Prompt Caching 运作良好 |
| `duration_ms > 0 && completion_tokens > 0 && completionTokensPerSecond < 15` | 生成速度较慢，可考虑使用更快模型 |
| `_reqMessages.length > 20` | 消息历史过长，可考虑摘要旧消息降低输入 token |

其中生成速度计算方式为：

$$
completionTokensPerSecond = \frac{completion\_tokens}{duration\_ms / 1000}
$$

如果以上规则都未命中，则显示兜底提示：当前未发现明显异常，建议继续按模型、模板、时段进行横向对比优化。

这意味着当前“优化建议”适合作为快速排查和成本/性能提示，不代表语义层面的回答质量评审，也不构成自动化 Prompt 评测结论。

#### 2.4.2 Prompts 页 Quality Score 计算方式

Prompts 页的 `Quality Score` 来自后端聚合统计，不依赖人工评分，也不是模型打分。它基于模板关联会话的可自动测量指标计算，实现在 `api/routers/prompts.py` 的 `_compute_quality_score(...)`。

评分构成如下：

- 稳定性：成功率 `success_rate`，满分 40 分
- 完整性：未被截断比例 `1 - truncation_rate`，满分 30 分
- 效率：输出/输入 token 比 `completion_prompt_ratio`，满分 30 分

完整公式为：

$$
QualityScore = round(40 \cdot successRate + 30 \cdot (1 - truncationRate) + 30 \cdot min(cpRatio / 0.5, 1.0))
$$

其中：

- `successRate = success_count / total_conversations`
- `truncationRate = truncated_count / total_conversations`
- `cpRatio = avg_completion_tokens / avg_prompt_tokens`

补充说明：

- 当 `cpRatio >= 0.5` 时，效率项按满分 30 分计算
- `avg_rating` 与 `rated_count` 会在模板详情中单独展示，但当前不参与 `Quality Score` 计算
- `Quality Score` 反映的是模板在稳定性、完整性、效率上的综合表现，而不是回答内容“好不好”的主观质量分

### 2.5 测试覆盖现状

- API 结构和版本元数据由 pytest 覆盖
- Dashboard 全页面已由 `tests/test_dashboard/test_dashboard_agent_browser.py` 做浏览器集成测试
- 当前自动化覆盖 Overview、Conversations、Costs、Latency、Models、Errors、Prompts、Analyzer 八个页面
- 测试同时验证 scoped hash 与 admin hash 的请求范围行为

## 3. 已清理项（本次合并）

- 清理过时描述：移除“latency/models 未接入前端”等历史状态
- 合并冗余路线：将重复的分阶段说明收敛为“当前能力 + 下一步方向”
- 保留一份 Dashboard 事实来源，避免与总开发计划重复冲突

## 4. 下一步优化方向

### 4.1 可视化深度

- 详情页增加时序视图（按消息时间/工具调用顺序）
- 增加跨会话对比（同模型、同模板、同标签）
- 增加可切换统计窗口（近 24h / 7d / 30d）

### 4.2 诊断能力

- 对话级 SLO 判定（慢请求、长上下文、异常成本）
- 错误与慢调用关联分析（status / model / prompt pattern）
- 请求配置与结果质量关联（参数-效果关系）

### 4.3 运营能力

- 支持保存筛选视图
- 支持共享链接与导出当前视图
- 支持基于标签/评分的回归样本集筛选

## 5. 版本标记约定

- Dashboard 页面头部版本号与 `pyproject.toml` 保持一致
- 每次 MINOR/PATCH 版本发布时，同步更新本文件“当前已落地能力”
