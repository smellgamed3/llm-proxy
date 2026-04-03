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


class SeqGenerator:
    """Thread-safe monotonically increasing sequence number generator.

    Initialises from MAX(seq) in the given table so restarts are safe.
    """

    _ALLOWED_TABLES = frozenset({"raw_requests", "raw_ws_connections"})

    def __init__(self, db_path: Path, table: str) -> None:
        if table not in self._ALLOWED_TABLES:
            raise ValueError(f"Unknown table for SeqGenerator: {table!r}")
        self._table = table
        self._lock = threading.Lock()
        self._counter = self._load_max_seq(db_path, table)

    def _load_max_seq(self, db_path: Path, table: str) -> int:
        try:
            conn = sqlite3.connect(str(db_path))
            # table name is validated against _ALLOWED_TABLES above
            row = conn.execute(f"SELECT MAX(seq) FROM {table}").fetchone()
            conn.close()
            return row[0] or 0
        except Exception:
            return 0

    def next(self) -> int:
        with self._lock:
            self._counter += 1
            return self._counter


class Recorder:
    """Records HTTP request/response pairs to SQLite (metadata) + JSONL bodies.

    Bodies are written to hourly-sharded JSONL files under ``bodies/``.
    A ``bodies/manifest.jsonl`` index records the file + byte offset for
    every body entry so downstream readers can locate data without scanning.
    """

    def __init__(self, config: Config):
        self.config = config
        self.log_dir = Path(config.log_dir)
        self.db_path = self.log_dir / "raw.db"
        self.bodies_dir = self.log_dir / "bodies"
        self.bodies_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self.bodies_dir / "manifest.jsonl"
        self._file_lock = threading.Lock()

        self._local = threading.local()
        self._init_db()

        self._http_seq = SeqGenerator(self.db_path, "raw_requests")
        self._ws_seq = SeqGenerator(self.db_path, "raw_ws_connections")

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
            CREATE TABLE IF NOT EXISTS raw_requests (
                id                 TEXT PRIMARY KEY,
                seq                INTEGER NOT NULL UNIQUE,
                timestamp          TEXT NOT NULL,
                duration_ms        REAL,
                method             TEXT NOT NULL,
                path               TEXT NOT NULL,
                query_string       TEXT,
                request_headers    TEXT,
                response_headers   TEXT,
                status_code        INTEGER,
                request_body_ref   TEXT,
                response_body_ref  TEXT,
                is_stream          INTEGER DEFAULT 0,
                request_body_size  INTEGER,
                response_body_size INTEGER,
                client_ip          TEXT,
                client_port        INTEGER,
                upstream_url       TEXT,
                error              TEXT,
                created_at         TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_raw_seq ON raw_requests(seq);
            CREATE INDEX IF NOT EXISTS idx_raw_timestamp ON raw_requests(timestamp);
            CREATE INDEX IF NOT EXISTS idx_raw_path ON raw_requests(path);
            CREATE INDEX IF NOT EXISTS idx_raw_status ON raw_requests(status_code);

            CREATE TABLE IF NOT EXISTS raw_ws_connections (
                id              TEXT PRIMARY KEY,
                seq             INTEGER NOT NULL UNIQUE,
                timestamp       TEXT NOT NULL,
                path            TEXT NOT NULL,
                query_string    TEXT,
                request_headers TEXT,
                subprotocol     TEXT,
                closed_at       TEXT,
                duration_ms     REAL,
                message_count   INTEGER DEFAULT 0,
                client_ip       TEXT,
                client_port     INTEGER,
                created_at      TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_raw_ws_seq ON raw_ws_connections(seq);
            CREATE INDEX IF NOT EXISTS idx_raw_ws_timestamp ON raw_ws_connections(timestamp);

            CREATE TABLE IF NOT EXISTS raw_ws_messages (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                connection_id TEXT NOT NULL,
                direction     TEXT NOT NULL,
                message_type  TEXT NOT NULL,
                data          TEXT,
                data_size     INTEGER,
                timestamp     TEXT NOT NULL,
                FOREIGN KEY (connection_id) REFERENCES raw_ws_connections(id)
            );
            CREATE INDEX IF NOT EXISTS idx_raw_ws_msg_conn ON raw_ws_messages(connection_id);
        """)
        conn.commit()

    def new_request_id(self) -> str:
        return str(uuid.uuid4())

    def _write_jsonl(self, record_id: str, direction: str, data: Any) -> tuple[str, int]:
        """Write a body to the hourly JSONL shard and update manifest.

        Returns ``(ref, original_size_bytes)``.
        """
        ref = f"{record_id}:{direction}"

        if isinstance(data, bytes):
            original_size = len(data)
            try:
                body_str = data.decode("utf-8")
            except UnicodeDecodeError:
                body_str = f"<binary {len(data)} bytes>"
        elif isinstance(data, str):
            original_size = len(data.encode("utf-8"))
            body_str = data
        else:
            original_size = 0
            body_str = ""

        # Truncate stored representation if too large
        if len(body_str) > self.config.max_body_log_size:
            body_str = (
                body_str[: self.config.max_body_log_size]
                + f"\n... [truncated at {self.config.max_body_log_size} bytes]"
            )

        now = datetime.now(timezone.utc)
        shard_name = now.strftime("%Y-%m-%d-%H") + ".jsonl"
        shard_path = self.bodies_dir / shard_name

        line = (
            json.dumps(
                {"ref": ref, "timestamp": now.isoformat(), "data": body_str},
                ensure_ascii=False,
            )
            + "\n"
        )
        line_bytes = line.encode("utf-8")

        with self._file_lock:
            offset = shard_path.stat().st_size if shard_path.exists() else 0
            with open(shard_path, "ab") as f:
                f.write(line_bytes)
            manifest_entry = (
                json.dumps(
                    {
                        "ref": ref,
                        "file": shard_name,
                        "offset": offset,
                        "length": len(line_bytes),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            with open(self._manifest_path, "a", encoding="utf-8") as f:
                f.write(manifest_entry)

        return ref, original_size

    def record_request(
        self,
        request_id: str,
        method: str,
        path: str,
        query_string: str,
        headers: dict,
        body: bytes | None,
        client_ip: str | None = None,
        client_port: int | None = None,
        upstream_url: str | None = None,
    ) -> None:
        """Record the incoming request (no JSON parsing performed)."""
        body_ref = None
        body_size = None
        if body:
            body_ref, body_size = self._write_jsonl(request_id, "request", body)

        seq = self._http_seq.next()
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO raw_requests
               (id, seq, timestamp, method, path, query_string,
                request_headers, request_body_ref, request_body_size,
                client_ip, client_port, upstream_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request_id,
                seq,
                datetime.now(timezone.utc).isoformat(),
                method,
                path,
                query_string,
                json.dumps(dict(headers), ensure_ascii=False),
                body_ref,
                body_size,
                client_ip,
                client_port,
                upstream_url,
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
        body_size = None
        if body:
            body_ref, body_size = self._write_jsonl(request_id, "response", body)

        conn = self._get_conn()
        conn.execute(
            """UPDATE raw_requests SET
               status_code = ?, response_headers = ?, response_body_ref = ?,
               is_stream = ?, duration_ms = ?, error = ?, response_body_size = ?
               WHERE id = ?""",
            (
                status_code,
                json.dumps(dict(headers), ensure_ascii=False),
                body_ref,
                1 if is_stream else 0,
                duration_ms,
                error,
                body_size,
                request_id,
            ),
        )
        conn.commit()
        logger.debug("Recorded response %s: %d (%.1fms)", request_id, status_code, duration_ms)

    def record_stream_body(self, request_id: str, accumulated_body: str) -> None:
        """Update the response body for a streamed response after completion."""
        body_ref, body_size = self._write_jsonl(request_id, "response", accumulated_body)
        conn = self._get_conn()
        conn.execute(
            "UPDATE raw_requests SET response_body_ref = ?, response_body_size = ? WHERE id = ?",
            (body_ref, body_size, request_id),
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
        client_ip: str | None = None,
        client_port: int | None = None,
    ) -> None:
        seq = self._ws_seq.next()
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO raw_ws_connections
               (id, seq, timestamp, path, query_string, request_headers,
                subprotocol, client_ip, client_port)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                conn_id,
                seq,
                datetime.now(timezone.utc).isoformat(),
                path,
                query_string,
                json.dumps(dict(headers), ensure_ascii=False),
                subprotocol,
                client_ip,
                client_port,
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
            data_size = len(data)
            # Store binary as base64
            raw = data[:max_size]
            text = base64.b64encode(raw).decode("ascii")
            if len(data) > max_size:
                text += f" [truncated, original {len(data)} bytes]"
        else:
            data_size = len(data.encode("utf-8"))
            text = data[:max_size]
            if len(data) > max_size:
                text += f"\n... [truncated at {max_size}]"

        conn = self._get_conn()
        conn.execute(
            """INSERT INTO raw_ws_messages
               (connection_id, direction, message_type, data, data_size, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (conn_id, direction, message_type, text, data_size, datetime.now(timezone.utc).isoformat()),
        )
        conn.execute(
            "UPDATE raw_ws_connections SET message_count = message_count + 1 WHERE id = ?",
            (conn_id,),
        )
        conn.commit()

    def record_ws_close(self, conn_id: str, duration_ms: float) -> None:
        conn = self._get_conn()
        conn.execute(
            """UPDATE raw_ws_connections SET closed_at = ?, duration_ms = ? WHERE id = ?""",
            (datetime.now(timezone.utc).isoformat(), duration_ms, conn_id),
        )
        conn.commit()
        logger.debug("WS close recorded %s (%.1fms)", conn_id[:8], duration_ms)
