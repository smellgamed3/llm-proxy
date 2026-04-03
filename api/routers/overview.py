from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends

from api.dependencies import get_analytics_db

router = APIRouter(tags=["overview"])


@router.get("/overview")
def get_overview(db: sqlite3.Connection = Depends(get_analytics_db)) -> dict:
    """Return high-level summary statistics."""
    row = db.execute("""
        SELECT
            COUNT(*) AS total_requests,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
            SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS error_count,
            SUM(COALESCE(cost_usd, 0)) AS total_cost_usd,
            AVG(COALESCE(duration_ms, 0)) AS avg_duration_ms,
            SUM(COALESCE(total_tokens, 0)) AS total_tokens
        FROM conversations
    """).fetchone()

    total = row["total_requests"] or 0
    success = row["success_count"] or 0
    success_rate = round(success / total, 4) if total > 0 else 0.0

    return {
        "total_requests": total,
        "success_count": success,
        "error_count": row["error_count"] or 0,
        "success_rate": success_rate,
        "total_cost_usd": round(row["total_cost_usd"] or 0.0, 6),
        "avg_duration_ms": round(row["avg_duration_ms"] or 0.0, 2),
        "total_tokens": row["total_tokens"] or 0,
    }


@router.get("/overview/daily")
def get_daily_overview(
    days: int = 7,
    db: sqlite3.Connection = Depends(get_analytics_db),
) -> list[dict]:
    """Return daily stats for the last N days."""
    rows = db.execute(
        """SELECT date, SUM(request_count) AS requests,
                  SUM(success_count) AS successes,
                  SUM(error_count) AS errors,
                  SUM(total_cost_usd) AS cost_usd,
                  AVG(avg_duration_ms) AS avg_latency_ms
           FROM daily_stats
           WHERE date >= date('now', ?)
           GROUP BY date
           ORDER BY date ASC""",
        (f"-{days} days",),
    ).fetchall()
    return [dict(r) for r in rows]
