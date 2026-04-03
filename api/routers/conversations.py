from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_analytics_db, get_raw_db, get_bodies_dir

router = APIRouter(tags=["conversations"])


@router.get("/conversations")
def list_conversations(
    page: int = 1,
    page_size: int = 50,
    model: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    template_id: str | None = None,
    sort: str = "timestamp",
    order: str = "desc",
    db: sqlite3.Connection = Depends(get_analytics_db),
) -> dict:
    """List conversations with filtering and pagination."""
    allowed_sort = {"timestamp", "duration_ms", "cost_usd", "total_tokens"}
    if sort not in allowed_sort:
        sort = "timestamp"
    order_dir = "DESC" if order.lower() == "desc" else "ASC"

    where_clauses: list[str] = []
    params: list[Any] = []

    if model:
        where_clauses.append("model = ?")
        params.append(model)
    if status:
        where_clauses.append("status = ?")
        params.append(status)
    if date_from:
        where_clauses.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        where_clauses.append("timestamp <= ?")
        params.append(date_to + "T23:59:59")
    if template_id:
        where_clauses.append("template_id = ?")
        params.append(template_id)
    if q:
        where_clauses.append("(user_prompt LIKE ? OR system_prompt LIKE ? OR assistant_response LIKE ?)")
        like_q = f"%{q}%"
        params.extend([like_q, like_q, like_q])

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    count_row = db.execute(
        f"SELECT COUNT(*) FROM conversations {where_sql}", params
    ).fetchone()
    total = count_row[0]

    offset = (page - 1) * page_size
    rows = db.execute(
        f"""SELECT id, seq, timestamp, path, method, provider, model, request_type,
                   status, error_type, status_code, is_stream, duration_ms,
                   prompt_tokens, completion_tokens, total_tokens, cost_usd,
                   template_id, finish_reason, has_tools, messages_count
            FROM conversations {where_sql}
            ORDER BY {sort} {order_dir}
            LIMIT ? OFFSET ?""",
        params + [page_size, offset],
    ).fetchall()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [dict(r) for r in rows],
    }


@router.get("/conversations/{conv_id}")
def get_conversation(
    conv_id: str,
    db: sqlite3.Connection = Depends(get_analytics_db),
    raw_db: sqlite3.Connection = Depends(get_raw_db),
) -> dict:
    """Get full conversation detail including extracted fields."""
    row = db.execute(
        "SELECT * FROM conversations WHERE id = ?", (conv_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    data = dict(row)
    if data.get("tools_list"):
        try:
            data["tools_list"] = json.loads(data["tools_list"])
        except Exception:
            pass
    return data


@router.get("/conversations/{conv_id}/raw")
def get_raw_conversation(
    conv_id: str,
    raw_db: sqlite3.Connection = Depends(get_raw_db),
) -> dict:
    """Get raw request/response data from raw.db."""
    row = raw_db.execute(
        "SELECT * FROM raw_requests WHERE id = ?", (conv_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Raw record not found")
    return dict(row)
