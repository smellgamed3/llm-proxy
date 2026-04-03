from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from api.dependencies import get_analytics_db

router = APIRouter(tags=["models"])


@router.get("/models/usage")
def get_model_usage(
    date_from: str | None = None,
    date_to: str | None = None,
    db: sqlite3.Connection = Depends(get_analytics_db),
) -> list[dict]:
    """Return request count, token usage, and cost per model."""
    where = []
    params = []
    if date_from:
        where.append("date >= ?")
        params.append(date_from)
    if date_to:
        where.append("date <= ?")
        params.append(date_to)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    rows = db.execute(
        f"""SELECT model, provider,
                   SUM(request_count) AS request_count,
                   SUM(success_count) AS success_count,
                   SUM(error_count) AS error_count,
                   SUM(total_tokens) AS total_tokens,
                   SUM(total_cost_usd) AS cost_usd,
                   AVG(avg_duration_ms) AS avg_duration_ms
            FROM daily_stats {where_sql}
            GROUP BY model, provider
            ORDER BY request_count DESC""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/models/list")
def list_models(
    db: sqlite3.Connection = Depends(get_analytics_db),
) -> list[str]:
    """Return distinct model names seen in conversations."""
    rows = db.execute(
        "SELECT DISTINCT model FROM conversations WHERE model IS NOT NULL ORDER BY model"
    ).fetchall()
    return [r["model"] for r in rows]
