from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import sqlite3
import threading
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config

logger = logging.getLogger("llm-proxy.recorder")


class SeqGenerator:
    """Thread-safe monotonically increasing sequence number generator."""

    def __init__(self, initial: int = 0):
        self._value = initial
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            self._value += 1
            return self._value

    @classmethod
    def from_db(cls, conn: sqlite3.Connection, table: str) -> "SeqGenerator":
        """Initialize from the MAX(seq) value in the given table."""
        try:
            row = conn.execute(f"SELECT MAX(seq) FROM {table}").fetchone()
            initial = row[0] if row and row[0] is not None else 0
        except Exception:
            initial = 0
        return cls(initial)


class Recorder:
    """Records HTTP request/response pairs to SQLite (metadata) + JSONL (bodies)."""

    def __init__(self, config: Config):
        self.config = config
        self.log_dir = Path(config.log_dir)

        self.db_path = self.log_dir / "raw.db"
        self.bodies_dir = self.log_dir / "bodies"

        self._local = threading.local()
        self._jsonl_lock = threading.Lock()
        self._init_db()
        self._seq = SeqGenerator.from_db(self._get_conn(), "raw_requests")
        self._ws_seq = SeqGenerator.from_db(self._get_conn(), "raw_ws_connections")

    @property
    def jsonl_path(self) -> Path:
        """Current JSONL file path (for backwards compatibility)."""
        now = datetime.now(timezone.utc)
        fname = now.strftime("%Y-%m-%d-%H") + ".jsonl"
        return self.bodies_dir / fname

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-32000")       # 32 MB page cache
            conn.execute("PRAGMA mmap_size=134217728")     # 128 MB mmap read
            conn.execute("PRAGMA wal_autocheckpoint=2000") # checkpoint every ~8 MB WAL
            conn.execute("PRAGMA temp_store=MEMORY")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        self.bodies_dir.mkdir(parents=True, exist_ok=True)
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS raw_requests (
                id                   TEXT PRIMARY KEY,
                seq                  INTEGER UNIQUE,
                timestamp            TEXT NOT NULL,
                method               TEXT NOT NULL,
                path                 TEXT NOT NULL,
                query_string         TEXT,
                request_headers      TEXT,
                request_body_ref     TEXT,
                request_body_size    INTEGER,
                status_code          INTEGER,
                response_headers     TEXT,
                response_body_ref    TEXT,
                response_body_size   INTEGER,
                is_stream            INTEGER DEFAULT 0,
                duration_ms          REAL,
                client_ip            TEXT,
                client_port          INTEGER,
                upstream_url         TEXT,
                provider             TEXT,
                error                TEXT,
                api_key_hash         TEXT,
                created_at           TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_raw_requests_timestamp ON raw_requests(timestamp);
            CREATE INDEX IF NOT EXISTS idx_raw_requests_path ON raw_requests(path);
            CREATE INDEX IF NOT EXISTS idx_raw_requests_seq ON raw_requests(seq);

            CREATE TABLE IF NOT EXISTS raw_ws_connections (
                id              TEXT PRIMARY KEY,
                seq             INTEGER UNIQUE,
                timestamp       TEXT NOT NULL,
                path            TEXT NOT NULL,
                query_string    TEXT,
                request_headers TEXT,
                subprotocol     TEXT,
                closed_at       TEXT,
                duration_ms     REAL,
                message_count   INTEGER DEFAULT 0,
                client_ip       TEXT,
                client_port     INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_raw_ws_timestamp ON raw_ws_connections(timestamp);
            CREATE INDEX IF NOT EXISTS idx_raw_ws_path ON raw_ws_connections(path);
            CREATE INDEX IF NOT EXISTS idx_raw_ws_seq ON raw_ws_connections(seq);

            CREATE TABLE IF NOT EXISTS raw_ws_messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                connection_id   TEXT NOT NULL,
                direction       TEXT NOT NULL,
                message_type    TEXT NOT NULL,
                data            TEXT,
                data_size       INTEGER,
                timestamp       TEXT NOT NULL,
                FOREIGN KEY (connection_id) REFERENCES raw_ws_connections(id)
            );
            CREATE INDEX IF NOT EXISTS idx_raw_ws_messages_conn ON raw_ws_messages(connection_id);
        """)
        self._migrate_db(conn)
        conn.commit()

    def _migrate_db(self, conn: sqlite3.Connection) -> None:
        raw_request_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(raw_requests)").fetchall()
        }
        if "api_key_hash" not in raw_request_columns:
            conn.execute("ALTER TABLE raw_requests ADD COLUMN api_key_hash TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_requests_api_key_hash ON raw_requests(api_key_hash)"
        )

    @staticmethod
    def extract_api_key_hash(headers: dict) -> str | None:
        """Extract and hash the API key from request headers (SHA-256, first 32 hex chars)."""
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
        else:
            key = (headers.get("x-api-key") or headers.get("X-Api-Key") or "").strip()
        if not key:
            return None
        return hashlib.sha256(key.encode()).hexdigest()[:32]

    def new_request_id(self) -> str:
        return str(uuid.uuid4())

    def _current_jsonl_path(self) -> Path:
        """Return the path for the current hour's JSONL shard."""
        now = datetime.now(timezone.utc)
        fname = now.strftime("%Y-%m-%d-%H") + ".jsonl"
        return self.bodies_dir / fname

    def _write_jsonl(self, record_id: str, direction: str, data: Any) -> tuple[str, int]:
        """Write a body to the hourly JSONL shard. Returns (ref, data_size)."""
        ref = f"{record_id}:{direction}"
        body_str = data if isinstance(data, str) else ""
        original_size = 0
        if isinstance(data, bytes):
            original_size = len(data)
            try:
                body_str = data.decode("utf-8")
            except UnicodeDecodeError:
                body_str = f"<binary {len(data)} bytes>"
        elif isinstance(data, str):
            original_size = len(data.encode("utf-8"))

        # Truncate if too large
        if len(body_str) > self.config.max_body_log_size:
            body_str = body_str[:self.config.max_body_log_size] + f"\n... [truncated at {self.config.max_body_log_size} bytes]"

        line = json.dumps({
            "ref": ref,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": body_str,
        }, ensure_ascii=False)
        line_bytes = (line + "\n").encode("utf-8")

        jsonl_path = self._current_jsonl_path()

        with self._jsonl_lock:
            offset = jsonl_path.stat().st_size if jsonl_path.exists() else 0
            with open(jsonl_path, "ab") as f:
                f.write(line_bytes)
                # No fsync: OS page-cache buffering is sufficient for analytics
                # logs. Forcing fsync here caused 2+ GB/s I/O spikes.

            # Update manifest
            manifest_path = self.bodies_dir / "manifest.jsonl"
            manifest_entry = json.dumps({
                "ref": ref,
                "file": jsonl_path.name,
                "offset": offset,
                "length": len(line_bytes),
            }, ensure_ascii=False)
            with open(manifest_path, "a", encoding="utf-8") as mf:
                mf.write(manifest_entry + "\n")
                # No fsync: manifest is rebuilt from JSONL on crash recovery.

        return ref, original_size

    @staticmethod
    def _decode_body_for_storage(data: str | bytes | None, headers: dict | None) -> str | bytes | None:
        """Decode compressed HTTP bodies before persisting JSONL shards."""
        if data is None or isinstance(data, str):
            return data

        encoding = ""
        if headers:
            encoding = str(
                headers.get("content-encoding")
                or headers.get("Content-Encoding")
                or ""
            ).lower().strip()

        try:
            if encoding == "gzip":
                return gzip.decompress(data)
            if encoding == "deflate":
                try:
                    return zlib.decompress(data)
                except zlib.error:
                    return zlib.decompress(data, -zlib.MAX_WBITS)
        except Exception as exc:
            logger.debug("Failed to decode %s body for storage: %s", encoding or "raw", exc)
        return data

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
        """Record the incoming request."""
        body_ref = None
        body_size = None
        if body:
            body_ref, body_size = self._write_jsonl(request_id, "request", body)

        api_key_hash = self.extract_api_key_hash(headers)
        seq = self._seq.next()
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO raw_requests (id, seq, timestamp, method, path, query_string,
               request_headers, request_body_ref, request_body_size,
               client_ip, client_port, upstream_url, api_key_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                api_key_hash,
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
            stored_body = self._decode_body_for_storage(body, headers)
            body_ref, body_size = self._write_jsonl(request_id, "response", stored_body)

        conn = self._get_conn()
        conn.execute(
            """UPDATE raw_requests SET
               status_code = ?, response_headers = ?, response_body_ref = ?,
               response_body_size = ?, is_stream = ?, duration_ms = ?, error = ?
               WHERE id = ?""",
            (
                status_code,
                json.dumps(dict(headers), ensure_ascii=False),
                body_ref,
                body_size,
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
               (id, seq, timestamp, path, query_string, request_headers, subprotocol,
                client_ip, client_port)
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
        data_size: int
        if isinstance(data, bytes):
            data_size = len(data)
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
            """INSERT INTO raw_ws_messages (connection_id, direction, message_type, data, data_size, timestamp)
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
