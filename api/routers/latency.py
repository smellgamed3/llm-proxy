from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from api.dependencies import get_analytics_db, resolve_auth, AuthContext
from api.query import SqlWhereBuilder

router = APIRouter(tags=["latency"])


@router.get("/latency/summary")
def get_latency_summary(
    model: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> dict:
    """Return latency percentiles (p50, p95, p99)."""
    filters = SqlWhereBuilder().add_auth(auth)
    filters.add("model = ?", model, enabled=bool(model))
    filters.add_date_range(date_from=date_from, date_to=date_to)
    filters.add("duration_ms IS NOT NULL")

    rows = db.execute(
        f"""SELECT duration_ms FROM conversations {filters.where_sql}
            ORDER BY duration_ms ASC""",
        filters.params,
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
    auth: AuthContext = Depends(resolve_auth),
) -> list[dict]:
    """Return avg latency per model."""
    filters = SqlWhereBuilder().add_auth(auth)
    rows = db.execute(
        f"""SELECT model, AVG(duration_ms) AS avg_ms, COUNT(*) AS count
           FROM conversations
           WHERE duration_ms IS NOT NULL {filters.and_sql}
           GROUP BY model
           ORDER BY avg_ms DESC""",
        filters.params,
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/latency/daily")
def get_daily_latency(
    days: int = 30,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> list[dict]:
    """Return daily average latency trend."""
    filters = SqlWhereBuilder().add_auth(auth)
    if auth.is_admin:
        rows = db.execute(
            """SELECT date, AVG(avg_duration_ms) AS avg_ms,
                      SUM(request_count) AS requests
               FROM daily_stats
               WHERE date >= date('now', ?)
               GROUP BY date
               ORDER BY date ASC""",
            (f"-{days} days",),
        ).fetchall()
    else:
        rows = db.execute(
            f"""SELECT date(timestamp) AS date,
                       AVG(COALESCE(duration_ms, 0)) AS avg_ms,
                       COUNT(*) AS requests
                FROM conversations
                WHERE timestamp >= date('now', ?) {filters.and_sql}
                GROUP BY date(timestamp)
                ORDER BY date ASC""",
            [f"-{days} days"] + filters.params,
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/latency/distribution")
def get_latency_distribution(
    model: str | None = None,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> list[dict]:
    """Return latency distribution in buckets for histogram."""
    filters = SqlWhereBuilder().add_auth(auth)
    filters.add("duration_ms IS NOT NULL")
    filters.add("model = ?", model, enabled=bool(model))

    buckets = [0, 100, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000]
    result = []
    for i in range(len(buckets)):
        lo = buckets[i]
        hi = buckets[i + 1] if i + 1 < len(buckets) else None
        if hi is not None:
            count = db.execute(
                f"SELECT COUNT(*) FROM conversations {filters.where_sql} AND duration_ms >= ? AND duration_ms < ?",
                filters.params + [lo, hi],
            ).fetchone()[0]
            label = f"{lo}-{hi}ms"
        else:
            count = db.execute(
                f"SELECT COUNT(*) FROM conversations {filters.where_sql} AND duration_ms >= ?",
                filters.params + [lo],
            ).fetchone()[0]
            label = f"{lo}ms+"
        result.append({"bucket": label, "count": count})
    return result
