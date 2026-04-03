from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("analyzer.store")


class AnalyticsStore:
    """Manages the analytics SQLite database."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id                  TEXT PRIMARY KEY,
                    seq                 INTEGER,
                    timestamp           TEXT NOT NULL,
                    path                TEXT,
                    method              TEXT,
                    provider            TEXT,
                    model               TEXT,
                    request_type        TEXT,
                    status              TEXT,
                    error_type          TEXT,
                    error_message       TEXT,
                    status_code         INTEGER,
                    is_stream           INTEGER DEFAULT 0,
                    duration_ms         REAL,
                    client_ip           TEXT,
                    upstream_url        TEXT,
                    prompt_tokens       INTEGER,
                    completion_tokens   INTEGER,
                    total_tokens        INTEGER,
                    cost_usd            REAL,
                    template_id         TEXT,
                    finish_reason       TEXT,
                    has_tools           INTEGER DEFAULT 0,
                    tools_list          TEXT,
                    messages_count      INTEGER,
                    temperature         REAL,
                    max_tokens          INTEGER,
                    system_prompt       TEXT,
                    user_prompt         TEXT,
                    assistant_response  TEXT,
                    created_at          TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_conv_timestamp ON conversations(timestamp);
                CREATE INDEX IF NOT EXISTS idx_conv_model ON conversations(model);
                CREATE INDEX IF NOT EXISTS idx_conv_status ON conversations(status);
                CREATE INDEX IF NOT EXISTS idx_conv_template ON conversations(template_id);
                CREATE INDEX IF NOT EXISTS idx_conv_seq ON conversations(seq);

                CREATE TABLE IF NOT EXISTS prompt_templates (
                    template_id         TEXT PRIMARY KEY,
                    system_prompt       TEXT,
                    first_seen          TEXT,
                    last_seen           TEXT,
                    use_count           INTEGER DEFAULT 0,
                    total_cost_usd      REAL DEFAULT 0.0,
                    avg_cost_usd        REAL DEFAULT 0.0
                );

                CREATE TABLE IF NOT EXISTS daily_stats (
                    date                TEXT NOT NULL,
                    model               TEXT,
                    provider            TEXT,
                    request_count       INTEGER DEFAULT 0,
                    success_count       INTEGER DEFAULT 0,
                    error_count         INTEGER DEFAULT 0,
                    total_tokens        INTEGER DEFAULT 0,
                    prompt_tokens       INTEGER DEFAULT 0,
                    completion_tokens   INTEGER DEFAULT 0,
                    total_cost_usd      REAL DEFAULT 0.0,
                    avg_duration_ms     REAL DEFAULT 0.0,
                    PRIMARY KEY (date, model, provider)
                );
                CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON daily_stats(date);

                CREATE TABLE IF NOT EXISTS watermark (
                    id          INTEGER PRIMARY KEY CHECK (id = 1),
                    seq         INTEGER NOT NULL DEFAULT 0,
                    processed   INTEGER NOT NULL DEFAULT 0,
                    updated_at  TEXT DEFAULT (datetime('now'))
                );
                INSERT OR IGNORE INTO watermark (id, seq, processed) VALUES (1, 0, 0);
            """)

    def get_watermark(self) -> int:
        with self._get_conn() as conn:
            row = conn.execute("SELECT seq FROM watermark WHERE id = 1").fetchone()
            return row["seq"] if row else 0

    def set_watermark(self, seq: int, records_processed: int) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE watermark SET seq = ?, processed = processed + ?,
                   updated_at = datetime('now') WHERE id = 1""",
                (seq, records_processed),
            )

    def upsert_conversation(self, data: dict) -> None:
        cols = list(data.keys())
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
        with self._get_conn() as conn:
            conn.execute(
                f"""INSERT INTO conversations ({col_names}) VALUES ({placeholders})
                    ON CONFLICT(id) DO UPDATE SET {updates}""",
                list(data.values()),
            )

    def upsert_prompt_template(
        self, template_id: str, system_prompt: str, data: dict
    ) -> None:
        with self._get_conn() as conn:
            existing = conn.execute(
                "SELECT * FROM prompt_templates WHERE template_id = ?",
                (template_id,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO prompt_templates
                       (template_id, system_prompt, first_seen, last_seen, use_count,
                        total_cost_usd, avg_cost_usd)
                       VALUES (?, ?, ?, ?, 1, ?, ?)""",
                    (
                        template_id,
                        system_prompt,
                        data.get("timestamp"),
                        data.get("timestamp"),
                        data.get("cost_usd", 0.0) or 0.0,
                        data.get("cost_usd", 0.0) or 0.0,
                    ),
                )
            else:
                cost = data.get("cost_usd", 0.0) or 0.0
                new_count = existing["use_count"] + 1
                new_total = (existing["total_cost_usd"] or 0.0) + cost
                conn.execute(
                    """UPDATE prompt_templates SET
                       last_seen = ?, use_count = ?,
                       total_cost_usd = ?, avg_cost_usd = ?
                       WHERE template_id = ?""",
                    (
                        data.get("timestamp"),
                        new_count,
                        new_total,
                        new_total / new_count if new_count else 0.0,
                        template_id,
                    ),
                )

    def refresh_daily_stats(self, date: str) -> None:
        """Rebuild daily_stats for the given date from conversations."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM daily_stats WHERE date = ?", (date,))
            conn.execute(
                """INSERT INTO daily_stats
                   (date, model, provider, request_count, success_count, error_count,
                    total_tokens, prompt_tokens, completion_tokens, total_cost_usd, avg_duration_ms)
                   SELECT
                       date(timestamp) as date,
                       COALESCE(model, 'unknown') as model,
                       COALESCE(provider, 'unknown') as provider,
                       COUNT(*) as request_count,
                       SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
                       SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) as error_count,
                       SUM(COALESCE(total_tokens, 0)) as total_tokens,
                       SUM(COALESCE(prompt_tokens, 0)) as prompt_tokens,
                       SUM(COALESCE(completion_tokens, 0)) as completion_tokens,
                       SUM(COALESCE(cost_usd, 0)) as total_cost_usd,
                       AVG(COALESCE(duration_ms, 0)) as avg_duration_ms
                   FROM conversations
                   WHERE date(timestamp) = ?
                   GROUP BY date(timestamp), model, provider""",
                (date,),
            )

    def reset(self) -> None:
        """Clear all analytics data."""
        with self._get_conn() as conn:
            conn.executescript("""
                DELETE FROM conversations;
                DELETE FROM prompt_templates;
                DELETE FROM daily_stats;
                UPDATE watermark SET seq = 0, processed = 0 WHERE id = 1;
            """)

    def get_status(self) -> dict:
        with self._get_conn() as conn:
            watermark_row = conn.execute("SELECT * FROM watermark WHERE id = 1").fetchone()
            conv_count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            template_count = conn.execute("SELECT COUNT(*) FROM prompt_templates").fetchone()[0]
            return {
                "watermark_seq": watermark_row["seq"] if watermark_row else 0,
                "records_processed": watermark_row["processed"] if watermark_row else 0,
                "conversation_count": conv_count,
                "template_count": template_count,
            }
