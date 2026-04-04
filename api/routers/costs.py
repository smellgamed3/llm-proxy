from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from api.dependencies import get_analytics_db, resolve_auth, AuthContext

router = APIRouter(tags=["costs"])


@router.get("/costs/summary")
def get_costs_summary(
    date_from: str | None = None,
    date_to: str | None = None,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> dict:
    """Return total cost summary, optionally filtered by date range."""
    where = []
    params = []
    key_where, key_params = auth.where_clause()
    if key_where:
        where.append(key_where)
        params.extend(key_params)
    if date_from:
        where.append("date(timestamp) >= ?")
        params.append(date_from)
    if date_to:
        where.append("date(timestamp) <= ?")
        params.append(date_to)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    row = db.execute(
        f"""SELECT SUM(COALESCE(cost_usd, 0)) AS total_cost_usd,
                   SUM(COALESCE(total_tokens, 0)) AS total_tokens,
                   COUNT(*) AS total_requests
            FROM conversations {where_sql}""",
        params,
    ).fetchone()
    return {
        "total_cost_usd": round(row["total_cost_usd"] or 0.0, 6),
        "total_tokens": row["total_tokens"] or 0,
        "total_requests": row["total_requests"] or 0,
    }


@router.get("/costs/by-model")
def get_costs_by_model(
    date_from: str | None = None,
    date_to: str | None = None,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> list[dict]:
    """Return cost breakdown by model."""
    where = []
    params = []
    key_where, key_params = auth.where_clause()
    if key_where:
        where.append(key_where)
        params.extend(key_params)
    if date_from:
        where.append("date(timestamp) >= ?")
        params.append(date_from)
    if date_to:
        where.append("date(timestamp) <= ?")
        params.append(date_to)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    rows = db.execute(
        f"""SELECT model, SUM(COALESCE(cost_usd, 0)) AS cost_usd,
                   SUM(COALESCE(total_tokens, 0)) AS total_tokens,
                   COUNT(*) AS request_count
            FROM conversations {where_sql}
            GROUP BY model
            ORDER BY cost_usd DESC""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/costs/daily")
def get_daily_costs(
    days: int = 30,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> list[dict]:
    """Return daily cost trend."""
    key_where, key_params = auth.where_clause()
    if auth.is_admin:
        rows = db.execute(
            """SELECT date, SUM(total_cost_usd) AS cost_usd,
                      SUM(total_tokens) AS total_tokens
               FROM daily_stats
               WHERE date >= date('now', ?)
               GROUP BY date
               ORDER BY date ASC""",
            (f"-{days} days",),
        ).fetchall()
    else:
        extra_where = f"AND {key_where}" if key_where else ""
        rows = db.execute(
            f"""SELECT date(timestamp) AS date,
                       SUM(COALESCE(cost_usd, 0)) AS cost_usd,
                       SUM(COALESCE(total_tokens, 0)) AS total_tokens
                FROM conversations
                WHERE timestamp >= date('now', ?) {extra_where}
                GROUP BY date(timestamp)
                ORDER BY date ASC""",
            [f"-{days} days"] + key_params,
        ).fetchall()
    return [dict(r) for r in rows]
