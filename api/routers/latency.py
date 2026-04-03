from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from api.dependencies import get_analytics_db

router = APIRouter(tags=["latency"])


@router.get("/latency/summary")
def get_latency_summary(
    model: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: sqlite3.Connection = Depends(get_analytics_db),
) -> dict:
    """Return latency percentiles (p50, p95, p99)."""
    where = []
    params = []
    if model:
        where.append("model = ?")
        params.append(model)
    if date_from:
        where.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        where.append("timestamp <= ?")
        params.append(date_to + "T23:59:59")
    where.append("duration_ms IS NOT NULL")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    rows = db.execute(
        f"""SELECT duration_ms FROM conversations {where_sql}
            ORDER BY duration_ms ASC""",
        params,
    ).fetchall()

    values = [r["duration_ms"] for r in rows]
    if not values:
        return {"p50": None, "p95": None, "p99": None, "count": 0, "avg": None}

    def percentile(lst: list[float], p: float) -> float:
        idx = int(len(lst) * p / 100)
        return lst[min(idx, len(lst) - 1)]

    return {
        "p50": round(percentile(values, 50), 2),
        "p95": round(percentile(values, 95), 2),
        "p99": round(percentile(values, 99), 2),
        "count": len(values),
        "avg": round(sum(values) / len(values), 2),
    }


@router.get("/latency/by-model")
def get_latency_by_model(
    db: sqlite3.Connection = Depends(get_analytics_db),
) -> list[dict]:
    """Return avg latency per model."""
    rows = db.execute(
        """SELECT model, AVG(duration_ms) AS avg_ms, COUNT(*) AS count
           FROM conversations
           WHERE duration_ms IS NOT NULL
           GROUP BY model
           ORDER BY avg_ms DESC""",
    ).fetchall()
    return [dict(r) for r in rows]
