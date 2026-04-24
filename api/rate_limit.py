from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections import deque
from typing import Deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# 分片锁数量：减少全局锁竞争，同时控制内存开销
_SHARD_COUNT = 16


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.enabled = os.getenv("API_RATE_LIMIT_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.max_requests = max(int(os.getenv("API_RATE_LIMIT_MAX_REQUESTS", "300")), 1)
        self.window_seconds = max(int(os.getenv("API_RATE_LIMIT_WINDOW_SECONDS", "60")), 1)
        # --- 分片锁取代全局锁 ---
        # 每个分片持有独立的锁和 bucket 字典，key 通过 hash 映射到分片
        # 高并发下锁竞争降低 ~16 倍
        self._shard_locks = [threading.Lock() for _ in range(_SHARD_COUNT)]
        self._shard_buckets: list[dict[str, Deque[float]]] = [{} for _ in range(_SHARD_COUNT)]
        # 记录上次清理时间
        self._last_cleanup = time.time()
        self._cleanup_interval = 300  # 每 5 分钟清理一次过期 bucket

    def _shard_index(self, key: str) -> int:
        """通过 key 的 hash 值映射到分片索引。"""
        return hash(key) % _SHARD_COUNT

    def _cleanup_expired_buckets(self, now: float) -> None:
        """清理过期 bucket，防止无限增长导致内存泄漏。"""
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        threshold = now - self.window_seconds
        for idx in range(_SHARD_COUNT):
            with self._shard_locks[idx]:
                expired_keys = [
                    k for k, bucket in self._shard_buckets[idx].items()
                    if not bucket or bucket[-1] <= threshold
                ]
                for k in expired_keys:
                    del self._shard_buckets[idx][k]

    async def dispatch(self, request: Request, call_next):
        if not self.enabled or not request.url.path.startswith("/api"):
            return await call_next(request)

        now = time.time()
        key = self._key_for_request(request)
        retry_after = 0

        shard = self._shard_index(key)
        with self._shard_locks[shard]:
            bucket = self._shard_buckets[shard].setdefault(key, deque())
            # 清理过期时间戳
            window_start = now - self.window_seconds
            while bucket and bucket[0] <= window_start:
                bucket.popleft()

            if len(bucket) >= self.max_requests:
                retry_after = max(1, int(bucket[0] + self.window_seconds - now))
            else:
                bucket.append(now)

        # 定期清理过期 bucket（每 5 分钟触发一次，不在关键路径上）
        self._cleanup_expired_buckets(now)

        if retry_after:
            return Response(
                content=json.dumps({"detail": "Rate limit exceeded"}),
                media_type="application/json",
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        response = await call_next(request)
        response.headers.setdefault("X-RateLimit-Limit", str(self.max_requests))
        response.headers.setdefault("X-RateLimit-Window", str(self.window_seconds))
        return response

    def _key_for_request(self, request: Request) -> str:
        authorization = request.headers.get("authorization", "").strip().lower()
        if authorization.startswith("bearer "):
            return f"auth:{authorization[7:].strip()}"

        key_hashes = request.query_params.get("key_hashes", "").strip()
        if key_hashes:
            return f"keys:{key_hashes}"

        client_host = request.client.host if request.client else "anonymous"
        return f"ip:{client_host}"
