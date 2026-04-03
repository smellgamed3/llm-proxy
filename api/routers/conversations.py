from __future__ import annotations

import csv
import io
import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from analyzer.body_reader import BodyReader
from api.dependencies import get_analytics_db, get_raw_db, get_bodies_dir

router = APIRouter(tags=["conversations"])


class RatingBody(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str | None = None


class TagsBody(BaseModel):
    tags: list[str]


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
    path_prefix: str | None = None,
    request_type: str | None = None,
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
    if path_prefix:
        where_clauses.append("path LIKE ?")
        params.append(f"{path_prefix}%")
    if request_type:
        where_clauses.append("request_type = ?")
        params.append(request_type)
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
                   template_id, finish_reason, has_tools, messages_count,
                   substr(coalesce(user_prompt, ''), 1, 160) AS user_prompt_preview,
                   substr(coalesce(assistant_response, ''), 1, 160) AS assistant_response_preview
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


@router.get("/conversations/export")
def export_conversations(
    fmt: str = "jsonl",
    model: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    template_id: str | None = None,
    limit: int = 10000,
    db: sqlite3.Connection = Depends(get_analytics_db),
):
    """Export conversations as JSONL or CSV."""
    if fmt not in ("jsonl", "csv"):
        raise HTTPException(status_code=400, detail="fmt must be jsonl or csv")

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

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    safe_limit = min(limit, 50000)

    rows = db.execute(
        f"""SELECT id, timestamp, path, model, provider, request_type,
                   status, error_type, status_code, is_stream, duration_ms,
                   prompt_tokens, completion_tokens, total_tokens, cost_usd,
                   template_id, finish_reason, rating, tags,
                   user_prompt, assistant_response
            FROM conversations {where_sql}
            ORDER BY timestamp DESC LIMIT ?""",
        params + [safe_limit],
    ).fetchall()

    if fmt == "jsonl":
        def generate_jsonl():
            for row in rows:
                yield json.dumps(dict(row), ensure_ascii=False) + "\n"

        return StreamingResponse(
            generate_jsonl(),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": "attachment; filename=conversations.jsonl"},
        )
    else:
        buf = io.StringIO()
        if rows:
            fieldnames = list(dict(rows[0]).keys())
            writer = csv.DictWriter(buf, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))

        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=conversations.csv"},
        )


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
    bodies_dir: str = Depends(get_bodies_dir),
) -> dict:
    """Get raw request/response data from raw.db."""
    row = raw_db.execute(
        "SELECT * FROM raw_requests WHERE id = ?", (conv_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Raw record not found")
    data = dict(row)
    body_reader = BodyReader(bodies_dir)
    if data.get("request_body_ref"):
        data["request_body"] = body_reader.read(data["request_body_ref"])
    if data.get("response_body_ref"):
        data["response_body"] = body_reader.read(data["response_body_ref"])
    return data


# ── Rating ─────────────────────────────────────────────────────────────────


@router.put("/conversations/{conv_id}/rating")
def set_rating(
    conv_id: str,
    body: RatingBody,
    db: sqlite3.Connection = Depends(get_analytics_db),
) -> dict:
    """Set or update a conversation rating (1-5) with optional comment."""
    existing = db.execute(
        "SELECT id FROM conversations WHERE id = ?", (conv_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.execute(
        "UPDATE conversations SET rating = ?, rating_comment = ? WHERE id = ?",
        (body.rating, body.comment, conv_id),
    )
    db.commit()
    return {"id": conv_id, "rating": body.rating, "comment": body.comment}


@router.delete("/conversations/{conv_id}/rating")
def delete_rating(
    conv_id: str,
    db: sqlite3.Connection = Depends(get_analytics_db),
) -> dict:
    """Clear a conversation rating."""
    existing = db.execute(
        "SELECT id FROM conversations WHERE id = ?", (conv_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.execute(
        "UPDATE conversations SET rating = NULL, rating_comment = NULL WHERE id = ?",
        (conv_id,),
    )
    db.commit()
    return {"id": conv_id, "rating": None}


# ── Tags ───────────────────────────────────────────────────────────────────


@router.put("/conversations/{conv_id}/tags")
def set_tags(
    conv_id: str,
    body: TagsBody,
    db: sqlite3.Connection = Depends(get_analytics_db),
) -> dict:
    """Set tags for a conversation (replaces existing)."""
    existing = db.execute(
        "SELECT id FROM conversations WHERE id = ?", (conv_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Conversation not found")
    tags_json = json.dumps(body.tags) if body.tags else None
    db.execute(
        "UPDATE conversations SET tags = ? WHERE id = ?",
        (tags_json, conv_id),
    )
    db.commit()
    return {"id": conv_id, "tags": body.tags}

