from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from .routers import overview, conversations, costs, latency, prompts, models, errors, admin
from .dependencies import verify_api_key


def create_app() -> FastAPI:
    app = FastAPI(title="LLM Proxy Analytics API", version="0.2.5")
    app.state.analyzer_sync_manager = admin.AnalyzerSyncManager()

    api_deps = [Depends(verify_api_key)]

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
