from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from api.dependencies import get_analytics_db, resolve_auth, AuthContext
from api.query import SqlWhereBuilder

router = APIRouter(tags=["models"])


@router.get("/models/usage")
def get_model_usage(
    date_from: str | None = None,
    date_to: str | None = None,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> list[dict]:
    """Return request count, token usage, and cost per model."""
    if auth.is_admin:
        filters = SqlWhereBuilder().add_date_range(
            column="date",
            date_from=date_from,
            date_to=date_to,
            cast_to_date=True,
        )
        rows = db.execute(
            f"""SELECT model, provider,
                       SUM(request_count) AS request_count,
                       SUM(success_count) AS success_count,
                       SUM(error_count) AS error_count,
                       SUM(total_tokens) AS total_tokens,
                       SUM(total_cost_usd) AS cost_usd,
                       AVG(avg_duration_ms) AS avg_duration_ms
                FROM daily_stats {filters.where_sql}
                GROUP BY model, provider
                ORDER BY request_count DESC""",
            filters.params,
        ).fetchall()
    else:
        filters = SqlWhereBuilder().add_auth(auth).add_date_range(
            column="timestamp",
            date_from=date_from,
            date_to=date_to,
            cast_to_date=True,
        )
        rows = db.execute(
            f"""SELECT COALESCE(model, 'unknown') AS model,
                       COALESCE(provider, 'unknown') AS provider,
                       COUNT(*) AS request_count,
                       SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
                       SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS error_count,
                       SUM(COALESCE(total_tokens, 0)) AS total_tokens,
                       SUM(COALESCE(cost_usd, 0)) AS cost_usd,
                       AVG(COALESCE(duration_ms, 0)) AS avg_duration_ms
                FROM conversations {filters.where_sql}
                GROUP BY model, provider
                ORDER BY request_count DESC""",
            filters.params,
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/models/list")
def list_models(
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> list[str]:
    """Return distinct model names seen in conversations."""
    filters = SqlWhereBuilder().add_auth(auth)
    rows = db.execute(
        f"SELECT DISTINCT model FROM conversations WHERE model IS NOT NULL {filters.and_sql} ORDER BY model",
        filters.params,
    ).fetchall()
    return [r["model"] for r in rows]
