# 参考仓库规则落地说明（litellm-searchlog）

> 参考来源：`smellgamed3/litellm-searchlog`
>
> 本文档定义当前项目在“LLM 请求解析提取”和“Dashboard 展示”两条链路中的统一规则，避免出现“请求已录制但页面为空”或“不同模型格式解析不一致”的问题。

## 1. 目标与范围

本规则覆盖以下模块：

- `analyzer/extractors/*.py`：请求与响应结构化提取
- `analyzer/worker.py`：提取结果入库与聚合
- `api/routers/conversations.py`：会话查询过滤能力
- `api/static/app.js` + `api/static/conversations.html`：详情展示、回退解析、可读性增强

不覆盖：

- 上游模型网关（LiteLLM / 其他 Provider）具体返回字段定义
- 成本单价策略（由 `pricing.yaml` 维护）

## 2. 数据提取规则

### 2.1 通用原则

1. 先尝试结构化 JSON 提取。
2. 若响应体是 SSE 文本（`data: ...`），按流式 chunk 解析。
3. 若标准字段缺失，使用回退字段（如 `reasoning_content`）。
4. 对于未知结构，不抛异常，返回尽量多的可用信息。

### 2.2 OpenAI 兼容路径识别

当前纳入 OpenAI 兼容提取器的路径：

- `/v1/chat/completions`
- `/v1/completions`
- `/v1/embeddings`

### 2.3 请求侧提取（request）

- `model`、`temperature`、`max_tokens`
- `request_type`
  - chat/completion/embedding
- chat 请求提取：
  - `messages_count`
  - `system_prompt`（最后一个 system）
  - `user_prompt`（最后一个 user）
- 工具调用声明提取：
  - `tools` 或 `functions`
  - 统一输出 `has_tools` + `tools_list`

### 2.4 响应侧提取（response）

#### 同步 JSON 响应

- usage：
  - `prompt_tokens`
  - `completion_tokens`
  - `total_tokens`
- choices：
  - `finish_reason`
  - `assistant_response`（优先 `choices[0].message.content`，回退 `choices[0].text`）

#### SSE 流式响应

- 自动识别条件：
  - `is_stream = 1`，或
  - 响应文本以 `data:` 开头（即使 `is_stream` 标记缺失）
- 按行解析所有 `data:` payload，忽略 `[DONE]`
- assistant 内容提取优先级：
  - `delta.content`
  - 回退 `delta.reasoning_content` / `delta.reasoning`
- usage：
  - 取最后一个包含 `usage` 的 chunk

### 2.5 错误状态分类

结合 HTTP 状态码和错误文本进行归一：

- `timeout`：408/504 或含 `timeout`
- `rate_limited`：429 或含 `rate limit`
- `success`：<400
- 其他：`error`

### 2.6 入库约束

- 提取失败不影响整条记录入库（保证可追溯）
- `tools_list` 以 JSON 字符串持久化
- `template_id` 由 `system_prompt` 指纹计算
- `cost_usd` 基于 model + token 使用量计算

## 3. Dashboard 展示规则

### 3.1 默认过滤策略

为避免 UI 静态资源噪声，Conversations 页默认聚焦 LLM 请求：

- `path_prefix=/v1/`
- `request_type=chat`

并支持额外过滤：

- model/status/template/date range/sort/order/page_size

### 3.2 展示字段优先级（关键）

对于 `system_prompt` / `user_prompt` / `assistant_response` / `tokens`：

1. 优先使用 analytics 库中的结构化字段（提取器结果）。
2. 若为空，回退到 raw 请求与响应体进行二次解析（页面层）。

#### 请求回退

- 从 `request_body.messages` 提取 system/user 内容
- 从 `request_body.tools/functions` 提取工具名

#### 响应回退

- JSON：`choices[0].message.content` / `choices[0].text`
- SSE：逐 chunk 合并 `delta.content`，回退 `delta.reasoning_content`
- usage：从 JSON 或最后 usage chunk 补齐 token 展示

### 3.3 详情页可视化

- 元信息：provider/model/status/template/finish_reason/cost/latency
- Token 分布条：Prompt vs Completion
- Prompt 优化提示：
  - system 过长
  - completion/prompt 比例异常
  - finish_reason=length
  - 输出冗长等
- Raw Request/Response + Headers 双栏
- 一键复制关键片段

## 4. 与参考仓库的一致性映射

| 参考仓库能力 | 当前项目对应实现 |
|---|---|
| `parseField` 容错解析 | `api/static/app.js` 中 `parseField` / `maybeJSON` |
| 从多来源提取 messages | `extractMessagesFromRequestBody` + 展示回退 |
| assistant 输出从 response 结构提取 | `extractAssistantFromResponseBody` |
| tool calls 友好展示 | `tools_list` + request 回退提取 |
| token/费用分层展示 | token breakdown + cost 字段（若 usage 可用） |
| 流式响应处理 | `OpenAICompatExtractor._parse_stream_response` + 前端 SSE 回退 |

## 5. 已知限制与建议

### 5.1 仍可能出现 token 为空

原因：部分上游流式响应不返回 usage。

处理建议：

- 网关开启 usage 回传（若支持）
- 或在采集层补充 token 统计（需要额外 tokenizer 成本）

### 5.2 噪声路径污染

若仍有大量 `/ui`、`/_next`、`favicon`：

- 在 `config.yaml` 里增加 `recording.include` 仅采集 `/v1/`
- 或在 `recording.exclude` 增加静态资源前缀

## 6. 验证清单（每次发布前）

1. 发送一条 `/v1/chat/completions` 请求
2. `api/conversations` 可见该记录（model/request_type/path 正确）
3. 详情页可见 user_prompt 与 assistant_response
4. SSE 场景下 assistant 输出非空
5. 全量测试通过

---

维护约定：

- 当引入新 Provider 或新响应格式时，先更新本规则文档，再更新提取器与测试。
- 文档、测试、实现三者必须同步。