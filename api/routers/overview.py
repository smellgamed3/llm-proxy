from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends

from api.dependencies import get_analytics_db, resolve_auth, AuthContext

router = APIRouter(tags=["overview"])


@router.get("/overview")
def get_overview(
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> dict:
    """Return high-level summary statistics."""
    key_where, key_params = auth.where_clause()
    where_sql = f"WHERE {key_where}" if key_where else ""

    row = db.execute(f"""
        SELECT
            COUNT(*) AS total_requests,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
            SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS error_count,
            SUM(COALESCE(cost_usd, 0)) AS total_cost_usd,
            AVG(COALESCE(duration_ms, 0)) AS avg_duration_ms,
            SUM(COALESCE(total_tokens, 0)) AS total_tokens
        FROM conversations {where_sql}
    """, key_params).fetchone()

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
    auth: AuthContext = Depends(resolve_auth),
) -> list[dict]:
    """Return daily stats for the last N days."""
    key_where, key_params = auth.where_clause()
    if auth.is_admin:
        # Admin: use pre-aggregated daily_stats for speed
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
    else:
        # Scoped: compute from conversations
        extra_where = f"AND {key_where}" if key_where else ""
        rows = db.execute(
            f"""SELECT date(timestamp) AS date,
                       COUNT(*) AS requests,
                       SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
                       SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS errors,
                       SUM(COALESCE(cost_usd, 0)) AS cost_usd,
                       AVG(COALESCE(duration_ms, 0)) AS avg_latency_ms
                FROM conversations
                WHERE timestamp >= date('now', ?) {extra_where}
                GROUP BY date(timestamp)
                ORDER BY date ASC""",
            [f"-{days} days"] + key_params,
        ).fetchall()
    return [dict(r) for r in rows]
