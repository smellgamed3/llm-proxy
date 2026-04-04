from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.enabled = os.getenv("API_RATE_LIMIT_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.max_requests = max(int(os.getenv("API_RATE_LIMIT_MAX_REQUESTS", "300")), 1)
        self.window_seconds = max(int(os.getenv("API_RATE_LIMIT_WINDOW_SECONDS", "60")), 1)
        self._lock = threading.Lock()
        self._buckets: dict[str, Deque[float]] = {}

    async def dispatch(self, request: Request, call_next):
        if not self.enabled or not request.url.path.startswith("/api"):
            return await call_next(request)

        now = time.time()
        key = self._key_for_request(request)
        retry_after = 0

        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            while bucket and bucket[0] <= now - self.window_seconds:
                bucket.popleft()

            if len(bucket) >= self.max_requests:
                retry_after = max(1, int(bucket[0] + self.window_seconds - now))
            else:
                bucket.append(now)
                if len(bucket) == 0:
                    self._buckets.pop(key, None)

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
