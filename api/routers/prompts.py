from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from api.dependencies import get_analytics_db

router = APIRouter(tags=["prompts"])


@router.get("/prompts/templates")
def list_prompt_templates(
    page: int = 1,
    page_size: int = 50,
    db: sqlite3.Connection = Depends(get_analytics_db),
) -> dict:
    """List prompt templates sorted by usage count."""
    count = db.execute("SELECT COUNT(*) FROM prompt_templates").fetchone()[0]
    offset = (page - 1) * page_size
    rows = db.execute(
        """SELECT template_id, first_seen, last_seen, use_count,
                  total_cost_usd, avg_cost_usd,
                  substr(system_prompt, 1, 200) AS system_prompt_preview
           FROM prompt_templates
           ORDER BY use_count DESC
           LIMIT ? OFFSET ?""",
        (page_size, offset),
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
) -> dict:
    from fastapi import HTTPException
    row = db.execute(
        "SELECT * FROM prompt_templates WHERE template_id = ?", (template_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")
    return dict(row)
