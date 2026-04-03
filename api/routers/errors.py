from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from api.dependencies import get_analytics_db

router = APIRouter(tags=["errors"])


@router.get("/errors/summary")
def get_errors_summary(
    date_from: str | None = None,
    date_to: str | None = None,
    db: sqlite3.Connection = Depends(get_analytics_db),
) -> dict:
    """Return error count and top error types."""
    where = []
    params = []
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

    errors = db.execute(
        f"""SELECT COUNT(*) FROM conversations
            {where_sql} {'AND' if where else 'WHERE'} status != 'success'""",
        params,
    ).fetchone()[0]

    top_types = db.execute(
        f"""SELECT error_type, COUNT(*) AS count
            FROM conversations
            {where_sql} {'AND' if where else 'WHERE'} error_type IS NOT NULL
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
) -> list[dict]:
    """Return most recent error conversations."""
    rows = db.execute(
        """SELECT id, timestamp, model, status, error_type, error_message,
                  status_code, duration_ms
           FROM conversations
           WHERE status != 'success'
           ORDER BY timestamp DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
