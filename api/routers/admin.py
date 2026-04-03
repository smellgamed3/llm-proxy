from __future__ import annotations

import os
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from analyzer.config import load_analyzer_config
from analyzer.worker import AnalyzerWorker
from analyzer.store import AnalyticsStore
from api.dependencies import get_analytics_db

router = APIRouter(tags=["admin"])


class RerunRequest(BaseModel):
    mode: Literal["incremental", "full", "range"] = "incremental"
    since: str | None = None
    until: str | None = None


def _build_status(db: sqlite3.Connection) -> dict:
    wm = db.execute("SELECT * FROM watermark WHERE id = 1").fetchone()
    conv_count = db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    template_count = db.execute("SELECT COUNT(*) FROM prompt_templates").fetchone()[0]
    return {
        "watermark_seq": wm["seq"] if wm else 0,
        "records_processed": wm["processed"] if wm else 0,
        "conversation_count": conv_count,
        "template_count": template_count,
    }


@router.get("/admin/status")
@router.get("/admin/analyzer/status")
def get_status(
    db: sqlite3.Connection = Depends(get_analytics_db),
) -> dict:
    """Return analytics system status."""
    return _build_status(db)


@router.post("/admin/reset")
def reset_analytics() -> dict:
    """Reset analytics database (clear all derived data)."""
    db_path = os.getenv("ANALYTICS_DB", "/data/analytics/analytics.db")
    store = AnalyticsStore(db_path)
    store.reset()
    return {"status": "reset complete"}


@router.post("/admin/analyzer/rerun")
def rerun_analyzer(request: RerunRequest) -> dict:
    """Run analyzer in one-shot mode for full/range/incremental catch-up."""
    config = load_analyzer_config()
    config.mode = request.mode
    config.since = request.since
    config.until = request.until
    result = AnalyzerWorker(config).run_once()
    return {
        "status": "completed",
        "mode": request.mode,
        "processed": result["processed"],
        "last_seq": result["last_seq"],
        "since": request.since,
        "until": request.until,
    }
