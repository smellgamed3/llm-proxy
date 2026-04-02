from __future__ import annotations

import base64
import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config

logger = logging.getLogger("llm-proxy.recorder")


class Recorder:
    """Records HTTP request/response pairs to SQLite (metadata) + JSONL (bodies)."""

    def __init__(self, config: Config):
        self.config = config
        self.log_dir = Path(config.log_dir)
        # log_dir is created by Config.__post_init__; Recorder just records the path

        self.db_path = self.log_dir / "proxy.db"
        self.jsonl_path = self.log_dir / "bodies.jsonl"

        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS requests (
                id              TEXT PRIMARY KEY,
                timestamp       TEXT NOT NULL,
                method          TEXT NOT NULL,
                path            TEXT NOT NULL,
                query_string    TEXT,
                request_headers TEXT,
                request_body_ref TEXT,
                status_code     INTEGER,
                response_headers TEXT,
                response_body_ref TEXT,
                is_stream       INTEGER DEFAULT 0,
                duration_ms     REAL,
                model           TEXT,
                provider        TEXT,
                error           TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_requests_timestamp ON requests(timestamp);
            CREATE INDEX IF NOT EXISTS idx_requests_model ON requests(model);
            CREATE INDEX IF NOT EXISTS idx_requests_path ON requests(path);

            CREATE TABLE IF NOT EXISTS ws_connections (
                id              TEXT PRIMARY KEY,
                timestamp       TEXT NOT NULL,
                path            TEXT NOT NULL,
                query_string    TEXT,
                request_headers TEXT,
                subprotocol     TEXT,
                closed_at       TEXT,
                duration_ms     REAL,
                message_count   INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_ws_timestamp ON ws_connections(timestamp);
            CREATE INDEX IF NOT EXISTS idx_ws_path ON ws_connections(path);

            CREATE TABLE IF NOT EXISTS ws_messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                connection_id   TEXT NOT NULL,
                direction       TEXT NOT NULL,
                message_type    TEXT NOT NULL,
                data            TEXT,
                timestamp       TEXT NOT NULL,
                FOREIGN KEY (connection_id) REFERENCES ws_connections(id)
            );
            CREATE INDEX IF NOT EXISTS idx_ws_messages_conn ON ws_messages(connection_id);
        """)
        conn.commit()

    def new_request_id(self) -> str:
        return str(uuid.uuid4())

    def _write_jsonl(self, record_id: str, direction: str, data: Any) -> str:
        """Write a body to the JSONL file. Returns a reference string."""
        ref = f"{record_id}:{direction}"
        body_str = data if isinstance(data, str) else ""
        if isinstance(data, bytes):
            try:
                body_str = data.decode("utf-8")
            except UnicodeDecodeError:
                body_str = f"<binary {len(data)} bytes>"

        # Truncate if too large
        if len(body_str) > self.config.max_body_log_size:
            body_str = body_str[:self.config.max_body_log_size] + f"\n... [truncated at {self.config.max_body_log_size} bytes]"

        line = json.dumps({
            "ref": ref,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": body_str,
        }, ensure_ascii=False)

        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        return ref

    def _extract_model(self, body: str | bytes | None) -> str | None:
        """Try to extract model name from request body."""
        if not body:
            return None
        try:
            text = body if isinstance(body, str) else body.decode("utf-8")
            data = json.loads(text)
            return data.get("model")
        except Exception:
            return None

    def record_request(
        self,
        request_id: str,
        method: str,
        path: str,
        query_string: str,
        headers: dict,
        body: bytes | None,
    ) -> None:
        """Record the incoming request."""
        body_ref = None
        if body:
            body_ref = self._write_jsonl(request_id, "request", body)

        model = self._extract_model(body)

        conn = self._get_conn()
        conn.execute(
            """INSERT INTO requests (id, timestamp, method, path, query_string,
               request_headers, request_body_ref, model)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request_id,
                datetime.now(timezone.utc).isoformat(),
                method,
                path,
                query_string,
                json.dumps(dict(headers), ensure_ascii=False),
                body_ref,
                model,
            ),
        )
        conn.commit()
        logger.debug("Recorded request %s: %s %s", request_id, method, path)

    def record_response(
        self,
        request_id: str,
        status_code: int,
        headers: dict,
        body: str | bytes | None,
        is_stream: bool,
        duration_ms: float,
        error: str | None = None,
    ) -> None:
        """Record the response (called after full response is received/streamed)."""
        body_ref = None
        if body:
            body_ref = self._write_jsonl(request_id, "response", body)

        conn = self._get_conn()
        conn.execute(
            """UPDATE requests SET
               status_code = ?, response_headers = ?, response_body_ref = ?,
               is_stream = ?, duration_ms = ?, error = ?
               WHERE id = ?""",
            (
                status_code,
                json.dumps(dict(headers), ensure_ascii=False),
                body_ref,
                1 if is_stream else 0,
                duration_ms,
                error,
                request_id,
            ),
        )
        conn.commit()
        logger.debug("Recorded response %s: %d (%.1fms)", request_id, status_code, duration_ms)

    def record_stream_body(self, request_id: str, accumulated_body: str) -> None:
        """Update the response body for a streamed response after completion."""
        body_ref = self._write_jsonl(request_id, "response", accumulated_body)
        conn = self._get_conn()
        conn.execute(
            "UPDATE requests SET response_body_ref = ? WHERE id = ?",
            (body_ref, request_id),
        )
        conn.commit()

    # ------------------------------------------------------------------ #
    # WebSocket recording                                                  #
    # ------------------------------------------------------------------ #

    def record_ws_connect(
        self,
        conn_id: str,
        path: str,
        query_string: str,
        headers: dict,
        subprotocol: str | None,
    ) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO ws_connections
               (id, timestamp, path, query_string, request_headers, subprotocol)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                conn_id,
                datetime.now(timezone.utc).isoformat(),
                path,
                query_string,
                json.dumps(dict(headers), ensure_ascii=False),
                subprotocol,
            ),
        )
        conn.commit()
        logger.debug("WS connect recorded %s: %s", conn_id[:8], path)

    def record_ws_message(
        self,
        conn_id: str,
        direction: str,
        message_type: str,
        data: str | bytes,
        max_size: int,
    ) -> None:
        if isinstance(data, bytes):
            # Store binary as base64
            raw = data[:max_size]
            text = base64.b64encode(raw).decode("ascii")
            if len(data) > max_size:
                text += f" [truncated, original {len(data)} bytes]"
        else:
            text = data[:max_size]
            if len(data) > max_size:
                text += f"\n... [truncated at {max_size}]"

        conn = self._get_conn()
        conn.execute(
            """INSERT INTO ws_messages (connection_id, direction, message_type, data, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (conn_id, direction, message_type, text, datetime.now(timezone.utc).isoformat()),
        )
        conn.execute(
            "UPDATE ws_connections SET message_count = message_count + 1 WHERE id = ?",
            (conn_id,),
        )
        conn.commit()

    def record_ws_close(self, conn_id: str, duration_ms: float) -> None:
        conn = self._get_conn()
        conn.execute(
            """UPDATE ws_connections SET closed_at = ?, duration_ms = ? WHERE id = ?""",
            (datetime.now(timezone.utc).isoformat(), duration_ms, conn_id),
        )
        conn.commit()
        logger.debug("WS close recorded %s (%.1fms)", conn_id[:8], duration_ms)
