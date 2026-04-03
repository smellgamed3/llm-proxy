from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routers import overview, conversations, costs, latency, prompts, models, errors, admin


def create_app() -> FastAPI:
    app = FastAPI(title="LLM Proxy Analytics API", version="0.2.0")

    app.include_router(overview.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")
    app.include_router(costs.router, prefix="/api")
    app.include_router(latency.router, prefix="/api")
    app.include_router(prompts.router, prefix="/api")
    app.include_router(models.router, prefix="/api")
    app.include_router(errors.router, prefix="/api")
    app.include_router(admin.router, prefix="/api")

    # Serve static dashboard
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
