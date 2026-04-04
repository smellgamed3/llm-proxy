from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from api.dependencies import get_analytics_db, resolve_auth, AuthContext

router = APIRouter(tags=["models"])


@router.get("/models/usage")
def get_model_usage(
    date_from: str | None = None,
    date_to: str | None = None,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> list[dict]:
    """Return request count, token usage, and cost per model."""
    key_where, key_params = auth.where_clause()
    if auth.is_admin:
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
    else:
        where = []
        params = list(key_params)
        if key_where:
            where.append(key_where)
        if date_from:
            where.append("date(timestamp) >= ?")
            params.append(date_from)
        if date_to:
            where.append("date(timestamp) <= ?")
            params.append(date_to)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        rows = db.execute(
            f"""SELECT COALESCE(model, 'unknown') AS model,
                       COALESCE(provider, 'unknown') AS provider,
                       COUNT(*) AS request_count,
                       SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
                       SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS error_count,
                       SUM(COALESCE(total_tokens, 0)) AS total_tokens,
                       SUM(COALESCE(cost_usd, 0)) AS cost_usd,
                       AVG(COALESCE(duration_ms, 0)) AS avg_duration_ms
                FROM conversations {where_sql}
                GROUP BY model, provider
                ORDER BY request_count DESC""",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/models/list")
def list_models(
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> list[str]:
    """Return distinct model names seen in conversations."""
    key_where, key_params = auth.where_clause()
    extra_where = f"AND {key_where}" if key_where else ""
    rows = db.execute(
        f"SELECT DISTINCT model FROM conversations WHERE model IS NOT NULL {extra_where} ORDER BY model",
        key_params,
    ).fetchall()
    return [r["model"] for r in rows]
