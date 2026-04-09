from __future__ import annotations

import logging
import os
from pathlib import Path
import time

from fastapi import Depends, FastAPI
from fastapi.requests import Request
from fastapi.staticfiles import StaticFiles

from common.logging import configure_logging
from .routers import overview, conversations, costs, latency, prompts, models, errors, admin
from .dependencies import resolve_auth
from .rate_limit import RateLimitMiddleware


logger = logging.getLogger("llm-proxy.api")


def create_app() -> FastAPI:
    configure_logging(service_name="api", level=os.getenv("LOG_LEVEL", "INFO"))
    app = FastAPI(title="LLM Proxy Analytics API", version="1.4.6")
    app.state.analyzer_sync_manager = admin.AnalyzerSyncManager()
    app.add_middleware(RateLimitMiddleware)

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000
        logger.info(
            "%s %s -> %s %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

    api_deps = [Depends(resolve_auth)]

    app.include_router(overview.router, prefix="/api", dependencies=api_deps)
    app.include_router(conversations.router, prefix="/api", dependencies=api_deps)
    app.include_router(costs.router, prefix="/api", dependencies=api_deps)
    app.include_router(latency.router, prefix="/api", dependencies=api_deps)
    app.include_router(prompts.router, prefix="/api", dependencies=api_deps)
    app.include_router(models.router, prefix="/api", dependencies=api_deps)
    app.include_router(errors.router, prefix="/api", dependencies=api_deps)
    app.include_router(admin.router, prefix="/api", dependencies=api_deps)

    # Serve static dashboard
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
