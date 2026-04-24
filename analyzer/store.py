from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("analyzer.store")


class AnalyticsStore:
    """Manages the analytics SQLite database."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Return the persistent write connection (lazily created).

        A single long-lived connection avoids the overhead of repeated
        sqlite3.connect / close cycles, each of which can trigger a WAL
        checkpoint and cause I/O spikes.
        """
        if self._conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-65536")       # 64 MB page cache
            conn.execute("PRAGMA mmap_size=268435456")     # 256 MB mmap read
            conn.execute("PRAGMA wal_autocheckpoint=4000") # checkpoint every ~16 MB WAL
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.row_factory = sqlite3.Row
            self._conn = conn
        return self._conn

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
                    rating              INTEGER,
                    rating_comment      TEXT,
                    tags                TEXT,
                    trace_id            TEXT,
                    parent_id           TEXT,
                    span_name           TEXT,
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

                CREATE TABLE IF NOT EXISTS sync_jobs (
                    job_id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    mode             TEXT NOT NULL,
                    since            TEXT,
                    until            TEXT,
                    status           TEXT NOT NULL,
                    progress         REAL DEFAULT 0.0,
                    processed_rows   INTEGER DEFAULT 0,
                    total_rows       INTEGER DEFAULT 0,
                    remaining_rows   INTEGER DEFAULT 0,
                    current_seq      INTEGER DEFAULT 0,
                    target_seq       INTEGER DEFAULT 0,
                    last_timestamp   TEXT,
                    started_at       TEXT,
                    finished_at      TEXT,
                    error            TEXT,
                    stop_requested   INTEGER DEFAULT 0,
                    created_at       TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_sync_jobs_started_at ON sync_jobs(started_at DESC);
            """)
            # Schema migrations for new columns
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add columns introduced in later versions (idempotent)."""
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(conversations)").fetchall()
        }
        migrations = [
            ("rating", "INTEGER"),
            ("rating_comment", "TEXT"),
            ("tags", "TEXT"),
            ("trace_id", "TEXT"),
            ("parent_id", "TEXT"),
            ("span_name", "TEXT"),
            ("api_key_hash", "TEXT"),
        ]
        for col, col_type in migrations:
            if col not in existing:
                conn.execute(
                    f"ALTER TABLE conversations ADD COLUMN {col} {col_type}"
                )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_trace ON conversations(trace_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_api_key_hash ON conversations(api_key_hash)"
        )

        sync_job_existing = {
            row[1] for row in conn.execute("PRAGMA table_info(sync_jobs)").fetchall()
        }
        sync_job_migrations = [
            ("progress", "REAL DEFAULT 0.0"),
            ("processed_rows", "INTEGER DEFAULT 0"),
            ("total_rows", "INTEGER DEFAULT 0"),
            ("remaining_rows", "INTEGER DEFAULT 0"),
            ("current_seq", "INTEGER DEFAULT 0"),
            ("target_seq", "INTEGER DEFAULT 0"),
            ("last_timestamp", "TEXT"),
            ("started_at", "TEXT"),
            ("finished_at", "TEXT"),
            ("error", "TEXT"),
            ("stop_requested", "INTEGER DEFAULT 0"),
        ]
        for col, col_type in sync_job_migrations:
            if col not in sync_job_existing:
                conn.execute(f"ALTER TABLE sync_jobs ADD COLUMN {col} {col_type}")

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

    def commit_batch_with_watermark(
        self,
        conv_list: list[dict],
        template_list: list[tuple[str, str, str | None, float | None]],
        daily_rows: list[tuple],
        watermark_seq: int,
        records_processed: int,
    ) -> None:
        """在一次数据库事务中完成批量 upsert + watermark 更新。

        确保 watermark 和实际数据写入原子化：崩溃恢复时不会因
        watermark 已推进但数据未写入而丢失记录，也不会因数据已写入
        但 watermark 未推进而重复处理。
        """
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            # 批量 upsert conversations
            if conv_list:
                cols = list(conv_list[0].keys())
                placeholders = ", ".join("?" for _ in cols)
                col_names = ", ".join(cols)
                updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
                sql_conv = (
                    f"INSERT INTO conversations ({col_names}) VALUES ({placeholders})"
                    f" ON CONFLICT(id) DO UPDATE SET {updates}"
                )
                conn.executemany(sql_conv, [list(d.values()) for d in conv_list])

            # 批量 upsert prompt_templates
            if template_list:
                agg: dict[str, dict] = {}
                for template_id, system_prompt, timestamp, cost in template_list:
                    cost = cost or 0.0
                    if template_id in agg:
                        entry = agg[template_id]
                        entry["count"] += 1
                        entry["total_cost"] += cost
                        if timestamp:
                            if not entry["last_ts"] or timestamp > entry["last_ts"]:
                                entry["last_ts"] = timestamp
                            if not entry["first_ts"] or timestamp < entry["first_ts"]:
                                entry["first_ts"] = timestamp
                    else:
                        agg[template_id] = {
                            "system_prompt": system_prompt,
                            "first_ts": timestamp,
                            "last_ts": timestamp,
                            "count": 1,
                            "total_cost": cost,
                        }

                sql_tpl = """INSERT INTO prompt_templates
                               (template_id, system_prompt, first_seen, last_seen,
                                use_count, total_cost_usd, avg_cost_usd)
                             VALUES (?, ?, ?, ?, ?, ?, ?)
                             ON CONFLICT(template_id) DO UPDATE SET
                               last_seen = MAX(excluded.last_seen, prompt_templates.last_seen),
                               use_count = prompt_templates.use_count + excluded.use_count,
                               total_cost_usd = prompt_templates.total_cost_usd + excluded.total_cost_usd,
                               avg_cost_usd = (prompt_templates.total_cost_usd + excluded.total_cost_usd)
                                              / (prompt_templates.use_count + excluded.use_count)"""
                tpl_rows = []
                for tid, entry in agg.items():
                    avg = entry["total_cost"] / entry["count"] if entry["count"] else 0.0
                    tpl_rows.append((
                        tid, entry["system_prompt"], entry["first_ts"],
                        entry["last_ts"], entry["count"], entry["total_cost"], avg,
                    ))
                conn.executemany(sql_tpl, tpl_rows)

            # 批量 upsert daily_stats
            if daily_rows:
                sql_daily = """INSERT INTO daily_stats
                               (date, model, provider, request_count, success_count, error_count,
                                total_tokens, prompt_tokens, completion_tokens, total_cost_usd, avg_duration_ms)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                             ON CONFLICT(date, model, provider) DO UPDATE SET
                               request_count = request_count + excluded.request_count,
                               success_count = success_count + excluded.success_count,
                               error_count = error_count + excluded.error_count,
                               total_tokens = total_tokens + excluded.total_tokens,
                               prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                               completion_tokens = completion_tokens + excluded.completion_tokens,
                               total_cost_usd = total_cost_usd + excluded.total_cost_usd,
                               avg_duration_ms = (avg_duration_ms * request_count + excluded.avg_duration_ms * excluded.request_count)
                                                 / (request_count + excluded.request_count)"""
                conn.executemany(sql_daily, daily_rows)

            # watermark 在同一事务中更新
            conn.execute(
                """UPDATE watermark SET seq = ?, processed = processed + ?,
                   updated_at = datetime('now') WHERE id = 1""",
                (watermark_seq, records_processed),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

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

    def upsert_conversations_batch(self, data_list: list[dict]) -> None:
        """Batch upsert conversations in a single transaction."""
        if not data_list:
            return
        cols = list(data_list[0].keys())
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
        sql = (
            f"INSERT INTO conversations ({col_names}) VALUES ({placeholders})"
            f" ON CONFLICT(id) DO UPDATE SET {updates}"
        )
        with self._get_conn() as conn:
            conn.executemany(sql, [list(d.values()) for d in data_list])

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

    def upsert_prompt_templates_batch(
        self, templates: list[tuple[str, str, str | None, float | None]]
    ) -> None:
        """Batch upsert prompt templates in a single transaction.

        Each tuple: ``(template_id, system_prompt, timestamp, cost_usd)``.
        Pre-aggregates duplicates within the batch before hitting the DB.
        """
        if not templates:
            return

        # Pre-aggregate by template_id within the batch
        agg: dict[str, dict] = {}
        for template_id, system_prompt, timestamp, cost in templates:
            cost = cost or 0.0
            if template_id in agg:
                entry = agg[template_id]
                entry["count"] += 1
                entry["total_cost"] += cost
                if timestamp:
                    if not entry["last_ts"] or timestamp > entry["last_ts"]:
                        entry["last_ts"] = timestamp
                    if not entry["first_ts"] or timestamp < entry["first_ts"]:
                        entry["first_ts"] = timestamp
            else:
                agg[template_id] = {
                    "system_prompt": system_prompt,
                    "first_ts": timestamp,
                    "last_ts": timestamp,
                    "count": 1,
                    "total_cost": cost,
                }

        sql = """INSERT INTO prompt_templates
                   (template_id, system_prompt, first_seen, last_seen,
                    use_count, total_cost_usd, avg_cost_usd)
                 VALUES (?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(template_id) DO UPDATE SET
                   last_seen = MAX(excluded.last_seen, prompt_templates.last_seen),
                   use_count = prompt_templates.use_count + excluded.use_count,
                   total_cost_usd = prompt_templates.total_cost_usd + excluded.total_cost_usd,
                   avg_cost_usd = (prompt_templates.total_cost_usd + excluded.total_cost_usd)
                                  / (prompt_templates.use_count + excluded.use_count)"""

        rows = []
        for template_id, entry in agg.items():
            avg = entry["total_cost"] / entry["count"] if entry["count"] else 0.0
            rows.append((
                template_id,
                entry["system_prompt"],
                entry["first_ts"],
                entry["last_ts"],
                entry["count"],
                entry["total_cost"],
                avg,
            ))

        with self._get_conn() as conn:
            conn.executemany(sql, rows)

    def refresh_daily_stats(self, date: str) -> None:
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

    def increment_daily_stats_batch(self, rows: list[tuple]) -> None:
        sql = """INSERT INTO daily_stats
                   (date, model, provider, request_count, success_count, error_count,
                    total_tokens, prompt_tokens, completion_tokens, total_cost_usd, avg_duration_ms)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(date, model, provider) DO UPDATE SET
                   request_count = request_count + excluded.request_count,
                   success_count = success_count + excluded.success_count,
                   error_count = error_count + excluded.error_count,
                   total_tokens = total_tokens + excluded.total_tokens,
                   prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                   completion_tokens = completion_tokens + excluded.completion_tokens,
                   total_cost_usd = total_cost_usd + excluded.total_cost_usd,
                   avg_duration_ms = (avg_duration_ms * request_count + excluded.avg_duration_ms * excluded.request_count)
                                     / (request_count + excluded.request_count)"""
        with self._get_conn() as conn:
            conn.executemany(sql, rows)

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

    def create_sync_job(
        self,
        *,
        mode: str,
        since: str | None,
        until: str | None,
        status: str,
        started_at: str | None,
    ) -> int:
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO sync_jobs (mode, since, until, status, started_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (mode, since, until, status, started_at),
            )
            return int(cursor.lastrowid)

    def update_sync_job(self, job_id: int, **updates: object) -> None:
        if not updates:
            return
        cols = []
        values: list[object] = []
        for key, value in updates.items():
            cols.append(f"{key} = ?")
            values.append(value)
        values.append(job_id)
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE sync_jobs SET {', '.join(cols)} WHERE job_id = ?",
                values,
            )

    def list_sync_jobs(self, limit: int = 20) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT job_id, mode, since, until, status, progress,
                          processed_rows, total_rows, remaining_rows,
                          current_seq, target_seq, last_timestamp,
                          started_at, finished_at, error, stop_requested
                   FROM sync_jobs
                   ORDER BY job_id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_sync_job(self, job_id: int) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute(
                """SELECT job_id, mode, since, until, status, progress,
                          processed_rows, total_rows, remaining_rows,
                          current_seq, target_seq, last_timestamp,
                          started_at, finished_at, error, stop_requested
                   FROM sync_jobs WHERE job_id = ?""",
                (job_id,),
            ).fetchone()
            return dict(row) if row else None
