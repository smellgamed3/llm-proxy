"""
Recorder — 支持两种模式：

1. **Async 模式**（生产环境）：通过 RecorderClient 发送记录到独立 recorder 进程。
2. **Sync 模式**（测试/开发环境）：直接写入 SQLite + JSONL，兼容原有测试。
   通过 RECORDER_SYNC=1 环境变量或 client=None 激活。
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import os
import sqlite3
import threading
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analyzer.body_reader import compress_body

from .config import Config

logger = logging.getLogger("llm-proxy.recorder")


# ---------------------------------------------------------------------------
# 通用辅助
# ---------------------------------------------------------------------------


def _body_to_send(data: str | bytes | None) -> str | None:
    """将 body 转为可通过 JSON 传输的字符串。"""
    if data is None:
        return None
    if isinstance(data, str):
        return data
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return f"<binary {len(data)} bytes>"


def _decode_body_for_storage(data: str | bytes | None, headers: dict | None) -> str | bytes | None:
    """解压 gzip/deflate 编码的 HTTP 响应体。"""
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
    except Exception:
        pass
    return data


# ---------------------------------------------------------------------------
# Sync SeqGenerator（仅 Sync 模式使用）
# ---------------------------------------------------------------------------


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
        try:
            row = conn.execute(f"SELECT MAX(seq) FROM {table}").fetchone()
            initial = row[0] if row and row[0] is not None else 0
        except Exception:
            initial = 0
        return cls(initial)


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


class Recorder:
    """记录器。

    - 有 RecorderClient → async fire-and-forget（生产模式）
    - 无 RecorderClient → 同步写入 SQLite + JSONL（测试模式）
    """

    def __init__(self, config: Config, client: Any = None):
        self.config = config
        self._client = client
        self.log_dir = Path(config.log_dir)
        self.db_path = self.log_dir / "raw.db"
        self.bodies_dir = self.log_dir / "bodies"

        if self._client is None:
            # Sync 模式：直接写入
            self._sync = True
            self._init_sync()
        else:
            self._sync = False

    # -- sync mode setup -------------------------------------------------

    def _init_sync(self) -> None:
        self._local = threading.local()
        self._jsonl_locks: dict[str, threading.Lock] = {}
        self._jsonl_locks_guard = threading.Lock()
        self._init_db()
        self._seq = SeqGenerator.from_db(self._get_conn(), "raw_requests")
        self._ws_seq = SeqGenerator.from_db(self._get_conn(), "raw_ws_connections")

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-32000")
            conn.execute("PRAGMA mmap_size=134217728")
            conn.execute("PRAGMA wal_autocheckpoint=2000")
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
        if "request_body" not in raw_request_columns:
            conn.execute("ALTER TABLE raw_requests ADD COLUMN request_body BLOB")
        if "response_body" not in raw_request_columns:
            conn.execute("ALTER TABLE raw_requests ADD COLUMN response_body BLOB")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_requests_api_key_hash ON raw_requests(api_key_hash)"
        )

    # -- sync JSONL write ------------------------------------------------

    def _current_jsonl_path(self) -> Path:
        now = datetime.now(timezone.utc)
        fname = now.strftime("%Y-%m-%d-%H") + ".jsonl"
        return self.bodies_dir / fname

    @property
    def jsonl_path(self) -> Path:
        return self._current_jsonl_path()

    def _write_jsonl(self, record_id: str, direction: str, data: Any) -> tuple[str, int]:
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

        if len(body_str) > self.config.max_body_log_size:
            body_str = body_str[:self.config.max_body_log_size] + f"\n... [truncated at {self.config.max_body_log_size} bytes]"

        line = json.dumps({
            "ref": ref,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": body_str,
        }, ensure_ascii=False)
        line_bytes = (line + "\n").encode("utf-8")

        jsonl_path = self._current_jsonl_path()
        manifest_path = self.bodies_dir / "manifest.jsonl"

        file_key = str(jsonl_path)
        with self._jsonl_locks_guard:
            lock = self._jsonl_locks.setdefault(file_key, threading.Lock())

        with lock:
            offset = jsonl_path.stat().st_size if jsonl_path.exists() else 0
            with open(jsonl_path, "ab") as f:
                f.write(line_bytes)

            manifest_entry = json.dumps({
                "ref": ref,
                "file": jsonl_path.name,
                "offset": offset,
                "length": len(line_bytes),
            }, ensure_ascii=False)
            with open(manifest_path, "a", encoding="utf-8") as mf:
                mf.write(manifest_entry + "\n")

        return ref, original_size

    @staticmethod
    def extract_api_key_hash(headers: dict) -> str | None:
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
        if self._sync:
            self._record_request_sync(request_id, method, path, query_string,
                                      headers, body, client_ip, client_port, upstream_url)
        else:
            api_key_hash = self.extract_api_key_hash(headers)
            self._client.send("record_request", {
                "id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "method": method,
                "path": path,
                "query_string": query_string,
                "headers": dict(headers),
                "body": _body_to_send(body),
                "client_ip": client_ip,
                "client_port": client_port,
                "upstream_url": upstream_url,
                "api_key_hash": api_key_hash,
            })

    def _record_request_sync(self, request_id, method, path, query_string,
                             headers, body, client_ip, client_port, upstream_url):
        body_ref = None
        body_size = None
        request_body_blob = None
        if body:
            body_ref, body_size = self._write_jsonl(request_id, "request", body)
            try:
                request_body_blob = compress_body(body.decode("utf-8"))
            except (UnicodeDecodeError, AttributeError):
                pass

        api_key_hash = self.extract_api_key_hash(headers)
        seq = self._seq.next()
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO raw_requests (id, seq, timestamp, method, path, query_string,
               request_headers, request_body_ref, request_body_size,
               request_body, client_ip, client_port, upstream_url, api_key_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                request_body_blob,
                client_ip,
                client_port,
                upstream_url,
                api_key_hash,
            ),
        )
        conn.commit()

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
        if self._sync:
            self._record_response_sync(request_id, status_code, headers, body,
                                       is_stream, duration_ms, error)
        else:
            stored_body = _decode_body_for_storage(body, headers)
            self._client.send("record_response", {
                "id": request_id,
                "status_code": status_code,
                "headers": dict(headers),
                "body": _body_to_send(stored_body),
                "is_stream": is_stream,
                "duration_ms": duration_ms,
                "error": error,
            })

    def _record_response_sync(self, request_id, status_code, headers, body,
                              is_stream, duration_ms, error):
        body_ref = None
        body_size = None
        response_body_blob = None
        if body:
            stored_body = _decode_body_for_storage(body, headers)
            body_ref, body_size = self._write_jsonl(request_id, "response", stored_body)
            try:
                body_str = stored_body if isinstance(stored_body, str) else stored_body.decode("utf-8")
                response_body_blob = compress_body(body_str)
            except (UnicodeDecodeError, AttributeError):
                pass

        conn = self._get_conn()
        conn.execute(
            """UPDATE raw_requests SET
               status_code = ?, response_headers = ?, response_body_ref = ?,
               response_body_size = ?, response_body = ?,
               is_stream = ?, duration_ms = ?, error = ?
               WHERE id = ?""",
            (
                status_code,
                json.dumps(dict(headers), ensure_ascii=False),
                body_ref,
                body_size,
                response_body_blob,
                1 if is_stream else 0,
                duration_ms,
                error,
                request_id,
            ),
        )
        conn.commit()

    def record_stream_body(self, request_id: str, accumulated_body: str) -> None:
        if self._sync:
            body_ref, body_size = self._write_jsonl(request_id, "response", accumulated_body)
            response_body_blob = compress_body(accumulated_body)
            conn = self._get_conn()
            conn.execute(
                "UPDATE raw_requests SET response_body_ref = ?, response_body_size = ?, response_body = ? WHERE id = ?",
                (body_ref, body_size, response_body_blob, request_id),
            )
            conn.commit()
        else:
            self._client.send("record_response", {
                "id": request_id,
                "status_code": None,
                "headers": {},
                "body": accumulated_body,
                "is_stream": True,
            })

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
        if self._sync:
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
        else:
            self._client.send("record_ws_connect", {
                "id": conn_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "path": path,
                "query_string": query_string,
                "headers": dict(headers),
                "subprotocol": subprotocol,
                "client_ip": client_ip,
                "client_port": client_port,
            })

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

        if self._sync:
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
        else:
            self._client.send("record_ws_message", {
                "connection_id": conn_id,
                "direction": direction,
                "message_type": message_type,
                "data": text,
                "data_size": data_size,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    def record_ws_close(self, conn_id: str, duration_ms: float) -> None:
        if self._sync:
            conn = self._get_conn()
            conn.execute(
                """UPDATE raw_ws_connections SET closed_at = ?, duration_ms = ? WHERE id = ?""",
                (datetime.now(timezone.utc).isoformat(), duration_ms, conn_id),
            )
            conn.commit()
        else:
            self._client.send("record_ws_close", {
                "id": conn_id,
                "closed_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": duration_ms,
            })

    def flush(self) -> None:
        pass

    def shutdown(self) -> None:
        if not self._sync and self._client:
            pass  # 异步客户端由 main.py 管理生命周期
