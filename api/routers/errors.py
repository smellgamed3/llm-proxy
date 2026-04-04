from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from api.dependencies import get_analytics_db, resolve_auth, AuthContext

router = APIRouter(tags=["errors"])


@router.get("/errors/summary")
def get_errors_summary(
    date_from: str | None = None,
    date_to: str | None = None,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> dict:
    """Return error count and top error types."""
    where = []
    params = []
    key_where, key_params = auth.where_clause()
    if key_where:
        where.append(key_where)
        params.extend(key_params)
    if date_from:
        where.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        where.append("timestamp <= ?")
        params.append(date_to + "T23:59:59")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total = db.execute(
        f"SELECT COUNT(*) FROM conversations {where_sql}", params
    ).fetchone()[0]

    error_where = f"{where_sql} {'AND' if where else 'WHERE'} status != 'success'"
    errors = db.execute(
        f"SELECT COUNT(*) FROM conversations {error_where}",
        params,
    ).fetchone()[0]

    top_types = db.execute(
        f"""SELECT error_type, COUNT(*) AS count
            FROM conversations
            {error_where} AND error_type IS NOT NULL
            GROUP BY error_type ORDER BY count DESC LIMIT 10""",
        params,
    ).fetchall()

    return {
        "total_requests": total,
        "error_count": errors,
        "error_rate": round(errors / total, 4) if total > 0 else 0.0,
        "top_error_types": [dict(r) for r in top_types],
    }


@router.get("/errors/recent")
def get_recent_errors(
    limit: int = 50,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> list[dict]:
    """Return most recent error conversations."""
    key_where, key_params = auth.where_clause()
    extra_where = f"AND {key_where}" if key_where else ""
    rows = db.execute(
        f"""SELECT id, timestamp, model, status, error_type, error_message,
                  status_code, duration_ms
           FROM conversations
           WHERE status != 'success' {extra_where}
           ORDER BY timestamp DESC LIMIT ?""",
        key_params + [limit],
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/errors/daily")
def get_errors_daily(
    days: int = 30,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> list[dict]:
    """Return daily error counts for chart."""
    key_where, key_params = auth.where_clause()
    extra_where = f"AND {key_where}" if key_where else ""
    rows = db.execute(
        f"""SELECT date(timestamp) AS date,
                  COUNT(*) AS error_count,
                  COUNT(DISTINCT error_type) AS error_types
           FROM conversations
           WHERE status != 'success' AND timestamp >= date('now', ?) {extra_where}
           GROUP BY date(timestamp)
           ORDER BY date ASC""",
        [f"-{days} days"] + key_params,
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/errors/by-type")
def get_errors_by_type(
    days: int = 30,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> list[dict]:
    """Return error distribution by type for pie chart."""
    key_where, key_params = auth.where_clause()
    extra_where = f"AND {key_where}" if key_where else ""
    rows = db.execute(
        f"""SELECT COALESCE(error_type, 'unknown') AS error_type,
                  COUNT(*) AS count
           FROM conversations
           WHERE status != 'success' AND timestamp >= date('now', ?) {extra_where}
           GROUP BY error_type
           ORDER BY count DESC LIMIT 15""",
        [f"-{days} days"] + key_params,
    ).fetchall()
    return [dict(r) for r in rows]
