from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from api.dependencies import get_analytics_db
from analyzer.store import AnalyticsStore
import os

router = APIRouter(tags=["admin"])


@router.get("/admin/status")
def get_status(
    db: sqlite3.Connection = Depends(get_analytics_db),
) -> dict:
    """Return analytics system status."""
    wm = db.execute("SELECT * FROM watermark WHERE id = 1").fetchone()
    conv_count = db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    template_count = db.execute("SELECT COUNT(*) FROM prompt_templates").fetchone()[0]
    return {
        "watermark_seq": wm["seq"] if wm else 0,
        "records_processed": wm["processed"] if wm else 0,
        "conversation_count": conv_count,
        "template_count": template_count,
    }


@router.post("/admin/reset")
def reset_analytics() -> dict:
    """Reset analytics database (clear all derived data)."""
    db_path = os.getenv("ANALYTICS_DB", "/data/analytics/analytics.db")
    store = AnalyticsStore(db_path)
    store.reset()
    return {"status": "reset complete"}
