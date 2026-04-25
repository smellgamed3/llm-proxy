"""
独立 Recorder 进程 — 单进程管理 SQLite + JSONL 写入。

架构：
  Proxy Workers (N 个) ──Unix Socket──> Recorder Server (1 个进程)
                                              │
                                         raw.db + bodies/*.jsonl

解决多 worker 下 SeqGenerator 冲突、manifest 并发损坏、多 writer 争锁问题。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sqlite3
import threading
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import orjson

from .recorder import _auto_migrate_bodies

logger = logging.getLogger("llm-proxy.recorderd")

_COMPRESS_LEVEL = 6


def _compress_body(data: str | None) -> bytes | None:
    """zlib 压缩 body 文本。返回 bytes 供 SQLite BLOB 列存储。"""
    if data is None:
        return None
    return zlib.compress(data.encode("utf-8"), _COMPRESS_LEVEL)

DEFAULT_SOCKET_PATH = "/var/run/llm-proxy/recorder.sock"

# ---------------------------------------------------------------------------
# JSONL 写入辅助
# ---------------------------------------------------------------------------


def _write_jsonl_line(
    bodies_dir: Path,
    max_body_log_size: int,
    *,
    record_id: str,
    direction: str,
    data: str | bytes | None,
    jsonl_locks: dict[str, threading.Lock],
) -> tuple[str | None, int | None]:
    """将 body 写入按小时分片的 JSONL。返回 (ref, data_size)。"""
    if data is None:
        return None, None

    ref = f"{record_id}:{direction}"
    body_str: str
    original_size = 0

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
        body_str = ""
        original_size = 0

    # 截断
    if len(body_str) > max_body_log_size:
        body_str = body_str[:max_body_log_size] + f"\n... [truncated at {max_body_log_size} bytes]"

    now = datetime.now(timezone.utc)
    fname = now.strftime("%Y-%m-%d-%H") + ".jsonl"
    jsonl_path = bodies_dir / fname

    line_obj = {
        "ref": ref,
        "timestamp": now.isoformat(),
        "data": body_str,
    }
    line_bytes = orjson.dumps(line_obj) + b"\n"

    # 按文件分片锁（不同小时不阻塞，单进程下锁开销极小）
    file_key = str(jsonl_path)
    lock = jsonl_locks.setdefault(file_key, threading.Lock())

    with lock:
        offset = jsonl_path.stat().st_size if jsonl_path.exists() else 0
        with open(jsonl_path, "ab") as f:
            f.write(line_bytes)

        # manifest 写入（全局锁保护，单进程安全）
        manifest_path = bodies_dir / "manifest.jsonl"
        manifest_entry = orjson.dumps({
            "ref": ref,
            "file": fname,
            "offset": offset,
            "length": len(line_bytes),
        })
        with open(manifest_path, "ab") as mf:
            mf.write(manifest_entry + b"\n")

    return ref, original_size


# ---------------------------------------------------------------------------
# SQLite 批量写入
# ---------------------------------------------------------------------------


class _BatchWriter:
    """单进程批量 SQLite 写入器（线程安全版本的精简实现）。"""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._buffer: list[tuple[str, tuple]] = []
        self._lock = threading.Lock()
        self._flush_interval = 0.5
        self._last_flush = time.monotonic()
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(self._db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-65536")       # 64 MB
            conn.execute("PRAGMA mmap_size=268435456")     # 256 MB
            conn.execute("PRAGMA wal_autocheckpoint=2000")
            conn.execute("PRAGMA temp_store=MEMORY")
            self._conn = conn
        return self._conn

    def enqueue(self, sql: str, params: tuple) -> None:
        with self._lock:
            self._buffer.append((sql, params))
            if len(self._buffer) >= 50:
                self._flush_locked()

    def flush_if_idle(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_flush
            if self._buffer and elapsed >= self._flush_interval:
                self._flush_locked()

    def _flush_locked(self) -> None:
        ops = self._buffer
        self._buffer = []
        if not ops:
            return
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for sql, params in ops:
                conn.execute(sql, params)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        self._last_flush = time.monotonic()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def shutdown(self) -> None:
        self.flush()
        if self._conn:
            self._conn.close()
            self._conn = None

    def execute(self, sql: str, params: tuple = ()) -> Any:
        """同步查询（仅用于初始化读取 MAX seq）。"""
        conn = self._get_conn()
        return conn.execute(sql, params)


# ---------------------------------------------------------------------------
# Recorder Server
# ---------------------------------------------------------------------------


class RecorderServer:
    """接收 proxy worker 的记录请求，写入 raw.db + JSONL。"""

    def __init__(
        self,
        *,
        log_dir: str,
        max_body_log_size: int = 10_485_760,
        socket_path: str = DEFAULT_SOCKET_PATH,
    ):
        self.bodies_dir = Path(log_dir) / "bodies"
        self.bodies_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = str(Path(log_dir) / "raw.db")
        self.max_body_log_size = max_body_log_size
        self.socket_path = socket_path

        self._writer = _BatchWriter(self.db_path)
        self._jsonl_locks: dict[str, threading.Lock] = {}
        self._init_db()

        # 从 DB 读取当前最大 seq
        row = self._writer.execute("SELECT MAX(seq) FROM raw_requests").fetchone()
        self._seq = (row[0] or 0) if row else 0
        row = self._writer.execute("SELECT MAX(seq) FROM raw_ws_connections").fetchone()
        self._ws_seq = (row[0] or 0) if row else 0

        # 统计
        self._records_written = 0
        self._ws_records_written = 0
        self._start_time = time.monotonic()

    def _init_db(self) -> None:
        conn = self._writer._get_conn()
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
            CREATE INDEX IF NOT EXISTS idx_raw_requests_api_key_hash ON raw_requests(api_key_hash);

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

        # 迁移：添加 api_key_hash 列
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(raw_requests)").fetchall()
        }
        if "api_key_hash" not in columns:
            conn.execute("ALTER TABLE raw_requests ADD COLUMN api_key_hash TEXT")
        if "request_body" not in columns:
            conn.execute("ALTER TABLE raw_requests ADD COLUMN request_body BLOB")
        if "response_body" not in columns:
            conn.execute("ALTER TABLE raw_requests ADD COLUMN response_body BLOB")

        _auto_migrate_bodies(conn, self.db_path, self.bodies_dir)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _next_ws_seq(self) -> int:
        self._ws_seq += 1
        return self._ws_seq

    # -- 消息处理 --------------------------------------------------------

    def _handle_record_request(self, data: dict) -> None:
        headers = data.get("headers", {})
        body = data.get("body")
        body = body.encode("utf-8") if isinstance(body, str) else body

        body_ref = None
        body_size = None
        request_body_blob = None
        if body:
            body_ref, body_size = _write_jsonl_line(
                self.bodies_dir, self.max_body_log_size,
                record_id=data["id"], direction="request", data=body,
                jsonl_locks=self._jsonl_locks,
            )
            try:
                request_body_blob = _compress_body(body.decode("utf-8"))
            except (UnicodeDecodeError, AttributeError):
                pass

        # 提取 API key hash
        api_key_hash = data.get("api_key_hash")
        if not api_key_hash:
            auth = headers.get("authorization") or headers.get("Authorization") or ""
            if auth.lower().startswith("bearer "):
                import hashlib
                key = auth[7:].strip()
                if key:
                    api_key_hash = hashlib.sha256(key.encode()).hexdigest()[:32]

        seq = self._next_seq()
        self._writer.enqueue(
            """INSERT INTO raw_requests (id, seq, timestamp, method, path, query_string,
               request_headers, request_body_ref, request_body_size,
               request_body, client_ip, client_port, upstream_url, api_key_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["id"],
                seq,
                data.get("timestamp", datetime.now(timezone.utc).isoformat()),
                data["method"],
                data["path"],
                data.get("query_string", ""),
                json.dumps(dict(headers), ensure_ascii=False),
                body_ref,
                body_size,
                request_body_blob,
                data.get("client_ip"),
                data.get("client_port"),
                data.get("upstream_url"),
                api_key_hash,
            ),
        )
        self._records_written += 1

    def _handle_record_response(self, data: dict) -> None:
        headers = data.get("headers", {})
        body = data.get("body")

        body_ref = None
        body_size = None
        response_body_blob = None
        if body:
            body_ref, body_size = _write_jsonl_line(
                self.bodies_dir, self.max_body_log_size,
                record_id=data["id"], direction="response", data=body,
                jsonl_locks=self._jsonl_locks,
            )
            try:
                response_body_blob = _compress_body(body)
            except Exception:
                pass

        self._writer.enqueue(
            """UPDATE raw_requests SET
               status_code = ?, response_headers = ?, response_body_ref = ?,
               response_body_size = ?, response_body = ?,
               is_stream = ?, duration_ms = ?, error = ?
               WHERE id = ?""",
            (
                data.get("status_code"),
                json.dumps(dict(headers), ensure_ascii=False),
                body_ref,
                body_size,
                response_body_blob,
                1 if data.get("is_stream") else 0,
                data.get("duration_ms"),
                data.get("error"),
                data["id"],
            ),
        )

    def _handle_record_ws_connect(self, data: dict) -> None:
        seq = self._next_ws_seq()
        self._writer.enqueue(
            """INSERT INTO raw_ws_connections
               (id, seq, timestamp, path, query_string, request_headers, subprotocol,
                client_ip, client_port)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["id"],
                seq,
                data.get("timestamp", datetime.now(timezone.utc).isoformat()),
                data["path"],
                data.get("query_string", ""),
                json.dumps(data.get("headers", {}), ensure_ascii=False),
                data.get("subprotocol"),
                data.get("client_ip"),
                data.get("client_port"),
            ),
        )
        self._ws_records_written += 1

    def _handle_record_ws_message(self, data: dict) -> None:
        self._writer.enqueue(
            """INSERT INTO raw_ws_messages (connection_id, direction, message_type, data, data_size, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                data["connection_id"],
                data["direction"],
                data["message_type"],
                data["data"],
                data["data_size"],
                data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            ),
        )
        self._writer.enqueue(
            "UPDATE raw_ws_connections SET message_count = message_count + 1 WHERE id = ?",
            (data["connection_id"],),
        )

    def _handle_record_ws_close(self, data: dict) -> None:
        self._writer.enqueue(
            """UPDATE raw_ws_connections SET closed_at = ?, duration_ms = ? WHERE id = ?""",
            (data.get("closed_at", datetime.now(timezone.utc).isoformat()), data.get("duration_ms"), data["id"]),
        )

    # -- 服务端 ----------------------------------------------------------

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = orjson.loads(line)
                except Exception:
                    continue
                cmd = msg.get("cmd")
                msg_data = msg.get("data", {})

                if cmd == "record_request":
                    self._handle_record_request(msg_data)
                elif cmd == "record_response":
                    self._handle_record_response(msg_data)
                elif cmd == "record_ws_connect":
                    self._handle_record_ws_connect(msg_data)
                elif cmd == "record_ws_message":
                    self._handle_record_ws_message(msg_data)
                elif cmd == "record_ws_close":
                    self._handle_record_ws_close(msg_data)

                # 定期 flush idle buffer
                self._writer.flush_if_idle()

        except Exception:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def serve(self) -> None:
        # 确保 socket 目录存在
        socket_dir = os.path.dirname(self.socket_path)
        if socket_dir:
            os.makedirs(socket_dir, exist_ok=True)

        # 清理旧 socket
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

        server = await asyncio.start_unix_server(
            self._handle_client,
            path=self.socket_path,
            limit=1024 * 1024,  # 1 MB buffer
        )

        # 定期 flush 定时器（处理无新连接时的 buffer 刷新）
        async def flush_loop():
            while True:
                await asyncio.sleep(0.5)
                self._writer.flush_if_idle()

        flash_task = asyncio.create_task(flush_loop())

        # 优雅关闭
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def _shutdown():
            logger.info("Recorder shutting down (received signal)")
            stop_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _shutdown)
            except NotImplementedError:
                pass

        logger.info("Recorder server listening on %s (request_seq=%d, ws_seq=%d)",
                     self.socket_path, self._seq, self._ws_seq)

        try:
            async with server:
                await stop_event.wait()
        finally:
            flash_task.cancel()
            try:
                await flash_task
            except asyncio.CancelledError:
                pass
            self._writer.shutdown()
            elapsed = time.monotonic() - self._start_time
            logger.info("Recorder stopped. Written: %d requests, %d WS connections in %.1fs",
                        self._records_written, self._ws_records_written, elapsed)

    @classmethod
    def run(cls, **kwargs) -> None:
        """同步入口，供 `python -m app.recorder_server` 使用。"""
        server = cls(**kwargs)
        asyncio.run(server.serve())


if __name__ == "__main__":
    import argparse
    from common.logging import configure_logging

    parser = argparse.ArgumentParser(description="LLM Proxy Recorder Server")
    parser.add_argument("--log-dir", default=os.environ.get("LOG_DIR", "/data/logs"))
    parser.add_argument("--max-body-log-size", type=int, default=int(os.environ.get("MAX_BODY_LOG_SIZE", "10485760")))
    parser.add_argument("--socket", default=os.environ.get("RECORDER_SOCKET", DEFAULT_SOCKET_PATH))
    args = parser.parse_args()

    configure_logging(service_name="recorder")
    RecorderServer.run(
        log_dir=args.log_dir,
        max_body_log_size=args.max_body_log_size,
        socket_path=args.socket,
    )
