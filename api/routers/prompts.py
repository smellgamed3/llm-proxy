from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_analytics_db, resolve_auth, AuthContext

router = APIRouter(tags=["prompts"])


@router.get("/prompts/templates")
def list_prompt_templates(
    page: int = 1,
    page_size: int = 50,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> dict:
    """List prompt templates sorted by usage count."""
    offset = (page - 1) * page_size
    if auth.is_admin:
        count = db.execute("SELECT COUNT(*) FROM prompt_templates").fetchone()[0]
        rows = db.execute(
            """SELECT template_id, first_seen, last_seen, use_count,
                      total_cost_usd, avg_cost_usd,
                      substr(system_prompt, 1, 200) AS system_prompt_preview
               FROM prompt_templates
               ORDER BY use_count DESC
               LIMIT ? OFFSET ?""",
            (page_size, offset),
        ).fetchall()
    else:
        key_where, key_params = auth.where_clause()
        # Only show templates the user has conversations for
        count = db.execute(
            f"""SELECT COUNT(DISTINCT template_id) FROM conversations
                WHERE template_id IS NOT NULL AND {key_where}""",
            key_params,
        ).fetchone()[0]
        rows = db.execute(
            f"""SELECT c.template_id, MIN(c.timestamp) AS first_seen,
                      MAX(c.timestamp) AS last_seen, COUNT(*) AS use_count,
                      SUM(COALESCE(c.cost_usd, 0)) AS total_cost_usd,
                      AVG(COALESCE(c.cost_usd, 0)) AS avg_cost_usd,
                      substr(pt.system_prompt, 1, 200) AS system_prompt_preview
               FROM conversations c
               LEFT JOIN prompt_templates pt ON c.template_id = pt.template_id
               WHERE c.template_id IS NOT NULL AND {key_where}
               GROUP BY c.template_id
               ORDER BY use_count DESC
               LIMIT ? OFFSET ?""",
            key_params + [page_size, offset],
        ).fetchall()
    return {
        "total": count,
        "page": page,
        "page_size": page_size,
        "items": [dict(r) for r in rows],
    }


@router.get("/prompts/templates/{template_id}")
def get_prompt_template(
    template_id: str,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> dict:
    row = db.execute(
        "SELECT * FROM prompt_templates WHERE template_id = ?", (template_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")
    if not auth.is_admin:
        # Check that the user has at least one conversation with this template
        key_where, key_params = auth.where_clause()
        has = db.execute(
            f"SELECT 1 FROM conversations WHERE template_id = ? AND {key_where} LIMIT 1",
            [template_id] + key_params,
        ).fetchone()
        if not has:
            raise HTTPException(status_code=404, detail="Template not found")
    return dict(row)


@router.get("/prompts/templates/{template_id}/stats")
def get_template_stats(
    template_id: str,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> dict:
    """Return aggregated quality stats for a prompt template."""
    key_where, key_params = auth.where_clause()
    extra_where = f"AND {key_where}" if key_where else ""
    row = db.execute(
        f"""SELECT
            COUNT(*) AS total_conversations,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
            AVG(COALESCE(duration_ms, 0)) AS avg_duration_ms,
            AVG(COALESCE(cost_usd, 0)) AS avg_cost_usd,
            SUM(COALESCE(cost_usd, 0)) AS total_cost_usd,
            AVG(COALESCE(total_tokens, 0)) AS avg_tokens,
            AVG(COALESCE(prompt_tokens, 0)) AS avg_prompt_tokens,
            AVG(COALESCE(completion_tokens, 0)) AS avg_completion_tokens,
            SUM(CASE WHEN finish_reason = 'length' THEN 1 ELSE 0 END) AS truncated_count,
            AVG(rating) AS avg_rating,
            COUNT(rating) AS rated_count
        FROM conversations WHERE template_id = ? {extra_where}""",
        [template_id] + key_params,
    ).fetchone()
    if not row or row["total_conversations"] == 0:
        raise HTTPException(status_code=404, detail="No conversations for template")
    total = row["total_conversations"]
    success = row["success_count"] or 0
    truncated = row["truncated_count"] or 0
    avg_prompt = row["avg_prompt_tokens"] or 0
    avg_compl = row["avg_completion_tokens"] or 0
    return {
        "total_conversations": total,
        "success_rate": round(success / total, 4) if total else 0,
        "avg_duration_ms": round(row["avg_duration_ms"] or 0, 2),
        "avg_cost_usd": round(row["avg_cost_usd"] or 0, 6),
        "total_cost_usd": round(row["total_cost_usd"] or 0, 6),
        "avg_tokens": round(row["avg_tokens"] or 0, 0),
        "avg_prompt_tokens": round(avg_prompt, 0),
        "avg_completion_tokens": round(avg_compl, 0),
        "completion_prompt_ratio": round(avg_compl / avg_prompt, 3) if avg_prompt > 0 else 0,
        "truncation_rate": round(truncated / total, 4) if total else 0,
        "avg_rating": round(row["avg_rating"], 2) if row["avg_rating"] is not None else None,
        "rated_count": row["rated_count"] or 0,
        # Quality score: higher is better (0-100)
        "quality_score": _compute_quality_score(
            success_rate=success / total if total else 0,
            truncation_rate=truncated / total if total else 0,
            cp_ratio=avg_compl / avg_prompt if avg_prompt > 0 else 0,
        ),
    }


def _compute_quality_score(
    success_rate: float,
    truncation_rate: float,
    cp_ratio: float,
) -> int:
    """Compute a 0-100 quality score from auto-measurable dimensions."""
    stability = success_rate * 40  # 40 points
    no_truncation = (1 - truncation_rate) * 30  # 30 points
    efficiency = min(cp_ratio / 0.5, 1.0) * 30  # 30 points (cp_ratio ≥0.5 = full)
    return round(stability + no_truncation + efficiency)


@router.get("/prompts/templates/{template_id}/conversations")
def list_template_conversations(
    template_id: str,
    page: int = 1,
    page_size: int = 20,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> dict:
    """List conversations using a specific prompt template."""
    key_where, key_params = auth.where_clause()
    extra_where = f"AND {key_where}" if key_where else ""
    count = db.execute(
        f"SELECT COUNT(*) FROM conversations WHERE template_id = ? {extra_where}",
        [template_id] + key_params,
    ).fetchone()[0]
    offset = (page - 1) * page_size
    rows = db.execute(
        f"""SELECT id, timestamp, model, status, duration_ms,
                  total_tokens, cost_usd, finish_reason, rating,
                  substr(coalesce(user_prompt, ''), 1, 120) AS user_prompt_preview,
                  substr(coalesce(assistant_response, ''), 1, 120) AS assistant_response_preview
           FROM conversations WHERE template_id = ? {extra_where}
           ORDER BY timestamp DESC LIMIT ? OFFSET ?""",
        [template_id] + key_params + [page_size, offset],
    ).fetchall()
    return {
        "total": count,
        "page": page,
        "page_size": page_size,
        "items": [dict(r) for r in rows],
    }


@router.get("/prompts/templates/{template_id}/daily")
def get_template_daily(
    template_id: str,
    days: int = 30,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> list[dict]:
    """Return daily usage trend for a specific template."""
    key_where, key_params = auth.where_clause()
    extra_where = f"AND {key_where}" if key_where else ""
    rows = db.execute(
        f"""SELECT date(timestamp) AS date,
                  COUNT(*) AS requests,
                  SUM(COALESCE(cost_usd, 0)) AS cost_usd,
                  AVG(COALESCE(duration_ms, 0)) AS avg_duration_ms
           FROM conversations
           WHERE template_id = ? AND timestamp >= date('now', ?) {extra_where}
           GROUP BY date(timestamp)
           ORDER BY date ASC""",
        [template_id, f"-{days} days"] + key_params,
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/prompts/compare")
def compare_templates(
    template_a: str,
    template_b: str,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> dict:
    """Compare two prompt templates side-by-side."""
    key_where, key_params = auth.where_clause()
    extra_where = f"AND {key_where}" if key_where else ""

    def _stats(tid: str) -> dict:
        tmpl = db.execute(
            "SELECT * FROM prompt_templates WHERE template_id = ?", (tid,)
        ).fetchone()
        if not tmpl:
            raise HTTPException(status_code=404, detail=f"Template {tid} not found")
        agg = db.execute(
            f"""SELECT COUNT(*) AS total,
                  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success,
                  AVG(COALESCE(duration_ms, 0)) AS avg_ms,
                  AVG(COALESCE(cost_usd, 0)) AS avg_cost,
                  SUM(COALESCE(cost_usd, 0)) AS total_cost,
                  AVG(COALESCE(total_tokens, 0)) AS avg_tokens,
                  AVG(rating) AS avg_rating
            FROM conversations WHERE template_id = ? {extra_where}""",
            [tid] + key_params,
        ).fetchone()
        total = agg["total"] or 0
        return {
            "template_id": tid,
            "system_prompt": tmpl["system_prompt"],
            "use_count": tmpl["use_count"],
            "total_conversations": total,
            "success_rate": round((agg["success"] or 0) / total, 4) if total else 0,
            "avg_duration_ms": round(agg["avg_ms"] or 0, 2),
            "avg_cost_usd": round(agg["avg_cost"] or 0, 6),
            "total_cost_usd": round(agg["total_cost"] or 0, 6),
            "avg_tokens": round(agg["avg_tokens"] or 0, 0),
            "avg_rating": round(agg["avg_rating"], 2) if agg["avg_rating"] is not None else None,
        }

    return {"a": _stats(template_a), "b": _stats(template_b)}


@router.get("/prompts/similar/{template_id}")
def find_similar_templates(
    template_id: str,
    db: sqlite3.Connection = Depends(get_analytics_db),
    auth: AuthContext = Depends(resolve_auth),
) -> list[dict]:
    """Find templates with similar system prompts using token overlap."""
    source = db.execute(
        "SELECT system_prompt FROM prompt_templates WHERE template_id = ?",
        (template_id,),
    ).fetchone()
    if not source or not source["system_prompt"]:
        raise HTTPException(status_code=404, detail="Template not found")

    source_tokens = set(source["system_prompt"].lower().split())
    if not source_tokens:
        return []

    others = db.execute(
        """SELECT template_id, system_prompt, use_count,
                  avg_cost_usd, last_seen
           FROM prompt_templates WHERE template_id != ?""",
        (template_id,),
    ).fetchall()

    results = []
    for row in others:
        other_prompt = row["system_prompt"] or ""
        other_tokens = set(other_prompt.lower().split())
        if not other_tokens:
            continue
        intersection = source_tokens & other_tokens
        union = source_tokens | other_tokens
        similarity = len(intersection) / len(union) if union else 0
        if similarity >= 0.3:
            results.append({
                "template_id": row["template_id"],
                "similarity": round(similarity, 3),
                "use_count": row["use_count"],
                "avg_cost_usd": row["avg_cost_usd"],
                "last_seen": row["last_seen"],
                "system_prompt_preview": other_prompt[:200],
            })

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:10]
