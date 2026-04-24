"""纯 ASGI 限流中间件（避免 BaseHTTPMiddleware 的 Task 开销）。

使用 16 路分片锁的滑动窗口算法。"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections import deque
from typing import Deque

# 分片锁数量
_SHARD_COUNT = 16


class RateLimitMiddleware:
    """纯 ASGI 中间件：滑动窗口限流。

    相比 BaseHTTPMiddleware 版本：
    - 不创建 asyncio.Task，减少事件循环开销
    - 直接 await self.app(scope, receive, send)，零额外内存分配
    """

    def __init__(self, app):
        self.app = app
        self.enabled = os.getenv("API_RATE_LIMIT_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.max_requests = max(int(os.getenv("API_RATE_LIMIT_MAX_REQUESTS", "300")), 1)
        self.window_seconds = max(int(os.getenv("API_RATE_LIMIT_WINDOW_SECONDS", "60")), 1)
        self._shard_locks = [threading.Lock() for _ in range(_SHARD_COUNT)]
        self._shard_buckets: list[dict[str, Deque[float]]] = [{} for _ in range(_SHARD_COUNT)]
        self._last_cleanup = time.time()
        self._cleanup_interval = 300

    def _shard_index(self, key: str) -> int:
        return hash(key) % _SHARD_COUNT

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not self.enabled or not scope.get("path", "").startswith("/api"):
            await self.app(scope, receive, send)
            return

        now = time.time()
        key = self._key_for_scope(scope)

        shard = self._shard_index(key)
        retry_after = 0
        with self._shard_locks[shard]:
            bucket = self._shard_buckets[shard].setdefault(key, deque())
            window_start = now - self.window_seconds
            while bucket and bucket[0] <= window_start:
                bucket.popleft()

            if len(bucket) >= self.max_requests:
                retry_after = max(1, int(bucket[0] + self.window_seconds - now))
            else:
                bucket.append(now)

        if retry_after:
            await self._send_429(send, retry_after)
            return

        await self.app(scope, receive, send)

    def _key_for_scope(self, scope) -> str:
        # 从 scope headers 提取 Authorization
        headers = dict(scope.get("headers", []))
        authorization = ""
        for k, v in scope.get("headers", []):
            if k == b"authorization":
                authorization = v.decode("latin-1").lower()
                break

        if authorization.startswith("bearer "):
            return f"auth:{authorization[7:].strip()}"

        # Query string key_hashes
        qs = scope.get("query_string", b"").decode("ascii")
        if qs:
            for pair in qs.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    if k == "key_hashes" and v:
                        return f"keys:{v}"

        client_host = scope.get("client", ("anonymous", 0))[0]
        return f"ip:{client_host}"

    async def _send_429(self, send, retry_after: int):
        body = json.dumps({"detail": "Rate limit exceeded"}).encode()
        await send({
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"retry-after", str(retry_after).encode()),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })
