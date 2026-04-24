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
from api.dependencies import get_analytics_db, get_raw_db, get_body_reader, resolve_auth, AuthContext
from api.query import SqlWhereBuilder, pagination_offset, validate_order, validate_sort

router = APIRouter(tags=["conversations"])


class RatingBody(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str | None = None


class TagsBody(BaseModel):
    tags: list[str]


def _conversation_filters(
    auth: AuthContext,
    *,
    model: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    template_id: str | None = None,
    path_prefix: str | None = None,
    request_type: str | None = None,
) -> SqlWhereBuilder:
    builder = SqlWhereBuilder().add_auth(auth)
    builder.add("model = ?", model, enabled=bool(model))
    builder.add("status = ?", status, enabled=bool(status))
    builder.add_date_range(date_from=date_from, date_to=date_to)
    builder.add("template_id = ?", template_id, enabled=bool(template_id))
    builder.add("path LIKE ?", f"{path_prefix}%", enabled=bool(path_prefix))
    builder.add("request_type = ?", request_type, enabled=bool(request_type))
    if q:
        # 使用 rowid 通过 FTS5 索引查找，替代 LIKE 全表扫描
        builder.add("rowid IN (SELECT rowid FROM conversations_fts WHERE conversations_fts MATCH ?)", q)
    return builder


def _scoped_conversation(
    db: sqlite3.Connection,
    conv_id: str,
    auth: AuthContext,
) -> sqlite3.Row:
    row = db.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not auth.is_admin and row["api_key_hash"] not in auth.key_hashes:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return row


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
    after_seq: int | None = None,
    sort: str = "timestamp",
    order: str = "desc",
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> dict:
    """List conversations with filtering and cursor-based pagination.

    当 after_seq 不为空时使用游标分页（比 OFFSET 更高效），否则使用传统分页。
    """
    sort = validate_sort(sort, allowed={"timestamp", "duration_ms", "cost_usd", "total_tokens", "seq"}, default="timestamp")
    order_dir = validate_order(order)
    filters = _conversation_filters(
        auth,
        model=model,
        status=status,
        date_from=date_from,
        date_to=date_to,
        q=q,
        template_id=template_id,
        path_prefix=path_prefix,
        request_type=request_type,
    )

    # COUNT(*)：始终从 conversations 表精确计数
    count_row = db.execute(
        f"SELECT COUNT(*) FROM conversations {filters.where_sql}", filters.params
    ).fetchone()
    total = count_row[0]

    # 游标分页（优先）或 OFFSET 分页
    cursor_clause = ""
    cursor_params: list[Any] = []
    if after_seq is not None:
        comparator = "<" if order_dir == "DESC" else ">"
        cursor_clause = f"AND seq {comparator} ? "
        cursor_params = [after_seq]

    rows = db.execute(
        f"""SELECT id, seq, timestamp, path, method, provider, model, request_type,
                   status, error_type, status_code, is_stream, duration_ms,
                   prompt_tokens, completion_tokens, total_tokens, cost_usd,
                   template_id, finish_reason, has_tools, messages_count,
                   substr(coalesce(user_prompt, ''), 1, 160) AS user_prompt_preview,
                   substr(coalesce(assistant_response, ''), 1, 160) AS assistant_response_preview
            FROM conversations {filters.where_sql}
            {cursor_clause}
            ORDER BY {sort} {order_dir}
            LIMIT ?{'' if cursor_clause else ' OFFSET ?'}""",
        filters.params + cursor_params + [page_size] + ([] if cursor_clause else [pagination_offset(page, page_size)]),
    ).fetchall()

    items = [dict(r) for r in rows]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
        "last_seq": items[-1]["seq"] if items else None,
    }


@router.get("/conversations/export")
def export_conversations(
    fmt: str = "jsonl",
    model: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    template_id: str | None = None,
    q: str | None = None,
    limit: int = 10000,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
):
    """Export conversations as JSONL or CSV."""
    if fmt not in ("jsonl", "csv"):
        raise HTTPException(status_code=400, detail="fmt must be jsonl or csv")

    filters = _conversation_filters(
        auth,
        model=model,
        status=status,
        date_from=date_from,
        date_to=date_to,
        template_id=template_id,
        q=q,
    )
    safe_limit = min(limit, 50000)

    rows = db.execute(
        f"""SELECT id, timestamp, path, model, provider, request_type,
                   status, error_type, status_code, is_stream, duration_ms,
                   prompt_tokens, completion_tokens, total_tokens, cost_usd,
                   template_id, finish_reason, rating, tags,
                   user_prompt, assistant_response
            FROM conversations {filters.where_sql}
            ORDER BY timestamp DESC LIMIT ?""",
        filters.params + [safe_limit],
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
    auth: AuthContext = Depends(resolve_auth),
) -> dict:
    """Get full conversation detail including extracted fields."""
    data = dict(_scoped_conversation(db, conv_id, auth))
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
    body_reader: BodyReader = Depends(get_body_reader),
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> dict:
    """Get raw request/response data from raw.db."""
    # Scope check via analytics conversation
    if not auth.is_admin:
        _scoped_conversation(db, conv_id, auth)

    row = raw_db.execute(
        "SELECT * FROM raw_requests WHERE id = ?", (conv_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Raw record not found")
    data = dict(row)
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
    auth: AuthContext = Depends(resolve_auth),
) -> dict:
    """Set or update a conversation rating (1-5) with optional comment."""
    _scoped_conversation(db, conv_id, auth)
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
    auth: AuthContext = Depends(resolve_auth),
) -> dict:
    """Clear a conversation rating."""
    _scoped_conversation(db, conv_id, auth)
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
    auth: AuthContext = Depends(resolve_auth),
) -> dict:
    """Set tags for a conversation (replaces existing)."""
    _scoped_conversation(db, conv_id, auth)
    tags_json = json.dumps(body.tags) if body.tags else None
    db.execute(
        "UPDATE conversations SET tags = ? WHERE id = ?",
        (tags_json, conv_id),
    )
    db.commit()
    return {"id": conv_id, "tags": body.tags}
