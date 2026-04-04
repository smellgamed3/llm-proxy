# 纯 API 调用说明

> 适用场景：不使用 Dashboard，只把 llm-proxy 作为代理层与查询 API 接入到自己的程序。

## 1. 两类接口

本项目对外暴露两组接口：

- 代理接口：`http://<host>:9090`
  业务流量发到这里，再由代理转发到真实 LLM provider。
- 查询接口：`http://<host>:9091/api/*`
  用于查询 conversations、models、costs、latency、errors 等分析结果。

## 2. 认证模型

查询 API 不直接接受原始 provider API key，而是接受 hash：

- scoped 访问：传 `key_hashes=<sha256(api_key)[:32]>`
- admin 访问：当某个 hash 与环境变量 `ADMIN_KEY_HASH` 相等时，拥有全量访问权限

计算 hash：

```bash
python3 - <<'PY'
import hashlib
api_key = 'YOUR_PROVIDER_KEY'
print(hashlib.sha256(api_key.encode()).hexdigest()[:32])
PY
```

如果你已经在 Dashboard 中手动添加过 API key，浏览器端也会自动计算同样的 hash 并存到本地。

## 3. 代理调用

### 3.1 curl

```bash
curl http://localhost:9090/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer YOUR_PROVIDER_KEY' \
  -d '{
    "model": "glm-5-turbo",
    "messages": [
      {"role": "user", "content": "hello"}
    ]
  }'
```

### 3.2 Python

```python
import httpx

payload = {
    "model": "glm-5-turbo",
    "messages": [{"role": "user", "content": "hello"}],
}

response = httpx.post(
    "http://localhost:9090/v1/chat/completions",
    headers={
        "Authorization": "Bearer YOUR_PROVIDER_KEY",
        "Content-Type": "application/json",
    },
    json=payload,
    timeout=120,
)
response.raise_for_status()
print(response.json())
```

### 3.3 JavaScript

```javascript
const response = await fetch('http://localhost:9090/v1/chat/completions', {
  method: 'POST',
  headers: {
    'Authorization': 'Bearer YOUR_PROVIDER_KEY',
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    model: 'glm-5-turbo',
    messages: [{ role: 'user', content: 'hello' }],
  }),
});

if (!response.ok) {
  throw new Error(`${response.status} ${response.statusText}`);
}

console.log(await response.json());
```

## 4. 查询 API 调用

### 4.1 overview

```bash
curl 'http://localhost:9091/api/overview?key_hashes=YOUR_KEY_HASH'
```

### 4.2 conversations 列表

```bash
curl 'http://localhost:9091/api/conversations?key_hashes=YOUR_KEY_HASH&page=1&page_size=20&date_from=2026-04-01'
```

### 4.3 conversation raw

```bash
curl 'http://localhost:9091/api/conversations/CONVERSATION_ID/raw?key_hashes=YOUR_KEY_HASH'
```

### 4.4 models usage

```bash
curl 'http://localhost:9091/api/models/usage?key_hashes=YOUR_KEY_HASH&date_from=2026-04-01'
```

### 4.5 admin status

```bash
curl 'http://localhost:9091/api/admin/status?key_hashes=ADMIN_KEY_HASH'
```

## 5. 推荐封装方式

建议把 provider key 与 query hash 分开管理：

- provider key：仅用于发到 `:9090` 的代理请求
- query hash：仅用于访问 `:9091/api/*`

一个常见做法是应用启动时先在服务端计算一次 hash，然后统一用于所有查询请求。

```python
import hashlib

provider_key = 'YOUR_PROVIDER_KEY'
query_hash = hashlib.sha256(provider_key.encode()).hexdigest()[:32]
```

## 6. 常见问题

### 6.1 401 Unauthorized — no key hashes

说明查询 API 请求里没有带 `key_hashes`，或者前端没有成功保存 hash。

检查点：

- URL 是否包含 `?key_hashes=...`
- 传的是 hash，而不是原始 provider key
- 如果走 Dashboard，浏览器本地存储里是否已有 `llm_proxy_key_hashes`

### 6.2 请求被转发成 `/v1/v1/chat/completions`

说明 `UPSTREAM_URL` 配错了。

正确：

```text
http://provider-host:3009
```

错误：

```text
http://provider-host:3009/v1
```

代理会自动拼接客户端原始路径，所以 `UPSTREAM_URL` 不要再追加 `/v1`。

### 6.3 Dashboard 里手动添加 key 没反应

如果是 `HTTP + IP` 访问，例如：

```text
http://192.168.50.189:9091
```

旧版本会因为浏览器没有 `crypto.subtle` 而无法计算 hash。`1.1.0` 已内置纯 JavaScript SHA-256 降级实现，这个场景现在可以直接添加原始 API key。

## 7. 最小联调顺序

```bash
# 1. 代理健康检查
curl http://localhost:9090/health

# 2. 通过代理发一个真实请求
curl http://localhost:9090/v1/chat/completions \
  -H 'Authorization: Bearer YOUR_PROVIDER_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"model":"glm-5-turbo","messages":[{"role":"user","content":"hi"}]}'

# 3. 等待 analyzer 处理
sleep 10

# 4. 用 hash 查 overview
curl 'http://localhost:9091/api/overview?key_hashes=YOUR_KEY_HASH'

# 5. 用 hash 查最新 conversation
curl 'http://localhost:9091/api/conversations?key_hashes=YOUR_KEY_HASH&page=1&page_size=1'
```

## 8. 相关文档

- [README.md](../README.md)
- [04-deployment.md](04-deployment.md)
- [05-operations.md](05-operations.md)