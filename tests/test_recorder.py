"""Tests for Recorder: SQLite schema, request/response recording, WS recording."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from unittest.mock import ANY

import pytest

from app.config import Config
from app.recorder import Recorder, SeqGenerator
from tests.conftest import db_rows, jsonl_bodies, jsonl_manifest


@pytest.fixture
def rec(tmp_path: Path) -> Recorder:
    cfg = Config(log_dir=str(tmp_path / "logs"))
    return Recorder(cfg)


class TestSeqGenerator:
    def test_monotonically_increasing(self):
        gen = SeqGenerator()
        values = [gen.next() for _ in range(10)]
        assert values == list(range(1, 11))

    def test_thread_safe(self):
        gen = SeqGenerator()
        results = []
        lock = threading.Lock()

        def worker():
            for _ in range(100):
                v = gen.next()
                with lock:
                    results.append(v)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(results) == list(range(1, 1001))
        assert len(set(results)) == 1000

    def test_initialized_from_db(self, rec: Recorder):
        rid1 = rec.new_request_id()
        rec.record_request(rid1, "GET", "/v1/models", "", {}, None)
        rid2 = rec.new_request_id()
        rec.record_request(rid2, "GET", "/v1/models", "", {}, None)

        # Create a new recorder pointing to the same DB — seq should continue
        cfg = Config(log_dir=str(rec.log_dir))
        rec2 = Recorder(cfg)
        rid3 = rec2.new_request_id()
        rec2.record_request(rid3, "GET", "/v1/models", "", {}, None)

        rows = db_rows(rec2, "raw_requests")
        seqs = sorted(r["seq"] for r in rows)
        assert seqs == [1, 2, 3]


class TestRecorderSchema:
    def test_tables_created(self, rec: Recorder):
        conn = sqlite3.connect(str(rec.db_path))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert {"raw_requests", "raw_ws_connections", "raw_ws_messages"} <= tables

    def test_db_path_in_log_dir(self, rec: Recorder):
        assert rec.db_path.parent == rec.log_dir
        assert rec.db_path.exists()

    def test_bodies_dir_created(self, rec: Recorder):
        assert rec.bodies_dir.exists()
        assert rec.bodies_dir.is_dir()

    def test_existing_raw_db_is_migrated_for_api_key_hash(self, tmp_path: Path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        legacy_db = log_dir / "raw.db"
        conn = sqlite3.connect(str(legacy_db))
        conn.executescript(
            """
            CREATE TABLE raw_requests (
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
                created_at           TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX idx_raw_requests_timestamp ON raw_requests(timestamp);
            CREATE INDEX idx_raw_requests_path ON raw_requests(path);
            CREATE INDEX idx_raw_requests_seq ON raw_requests(seq);

            CREATE TABLE raw_ws_connections (
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
            CREATE TABLE raw_ws_messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                connection_id   TEXT NOT NULL,
                direction       TEXT NOT NULL,
                message_type    TEXT NOT NULL,
                data            TEXT,
                data_size       INTEGER,
                timestamp       TEXT NOT NULL,
                FOREIGN KEY (connection_id) REFERENCES raw_ws_connections(id)
            );
            """
        )
        conn.commit()
        conn.close()

        rec = Recorder(Config(log_dir=str(log_dir)))
        columns = {
            row[1]
            for row in sqlite3.connect(str(rec.db_path)).execute(
                "PRAGMA table_info(raw_requests)"
            ).fetchall()
        }

        assert "api_key_hash" in columns


class TestRecordRequest:
    def test_basic_fields_stored(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(
            request_id=rid,
            method="POST",
            path="/v1/chat/completions",
            query_string="",
            headers={"content-type": "application/json"},
            body=b'{"model": "gpt-4o", "messages": []}',
        )
        rows = db_rows(rec, "raw_requests")
        assert len(rows) == 1
        r = rows[0]
        assert r["id"] == rid
        assert r["method"] == "POST"
        assert r["path"] == "/v1/chat/completions"

    def test_model_not_extracted_by_recorder(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {},
                           b'{"model": "claude-3-5-sonnet"}')
        rows = db_rows(rec, "raw_requests")
        # recorder does not extract model; no model column in raw_requests
        assert "model" not in rows[0]

    def test_seq_assigned(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "GET", "/v1/models", "", {}, None)
        rows = db_rows(rec, "raw_requests")
        assert rows[0]["seq"] == 1

    def test_client_ip_and_port_stored(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "GET", "/v1/models", "", {}, None,
                           client_ip="1.2.3.4", client_port=54321)
        rows = db_rows(rec, "raw_requests")
        assert rows[0]["client_ip"] == "1.2.3.4"
        assert rows[0]["client_port"] == 54321

    def test_upstream_url_stored(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {}, None,
                           upstream_url="https://api.openai.com")
        rows = db_rows(rec, "raw_requests")
        assert rows[0]["upstream_url"] == "https://api.openai.com"

    def test_body_written_to_jsonl(self, rec: Recorder):
        rid = rec.new_request_id()
        body = b'{"model": "gpt-4", "messages": []}'
        rec.record_request(rid, "POST", "/v1/chat", "", {}, body)
        entries = jsonl_bodies(rec)
        assert any(rid in e["ref"] and "gpt-4" in e["data"] for e in entries)

    def test_body_size_recorded(self, rec: Recorder):
        rid = rec.new_request_id()
        body = b'{"model": "gpt-4"}'
        rec.record_request(rid, "POST", "/v1/chat", "", {}, body)
        rows = db_rows(rec, "raw_requests")
        assert rows[0]["request_body_size"] == len(body)

    def test_no_body_no_jsonl_entry(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "GET", "/v1/models", "", {}, None)
        entries = jsonl_bodies(rec)
        assert not any(rid in e["ref"] for e in entries)

    def test_manifest_written(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {}, b'hello')
        entries = jsonl_manifest(rec)
        assert len(entries) == 1
        assert entries[0]["ref"].startswith(rid)
        assert "file" in entries[0]
        assert "offset" in entries[0]
        assert "length" in entries[0]

    def test_jsonl_writes_fsync_data_and_manifest(self, rec: Recorder, monkeypatch: pytest.MonkeyPatch):
        fsync_calls: list[int] = []

        def fake_fsync(fd: int) -> None:
            fsync_calls.append(fd)

        monkeypatch.setattr("app.recorder.os.fsync", fake_fsync)
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {}, b"hello")
        assert len(fsync_calls) >= 2


class TestRecordResponse:
    def test_response_updates_request_row(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {}, b'{"model":"m"}')
        rec.record_response(
            request_id=rid,
            status_code=200,
            headers={"content-type": "application/json"},
            body=b'{"id": "chatcmpl-1"}',
            is_stream=False,
            duration_ms=123.4,
        )
        rows = db_rows(rec, "raw_requests")
        assert rows[0]["status_code"] == 200
        assert abs(rows[0]["duration_ms"] - 123.4) < 0.01
        assert rows[0]["is_stream"] == 0

    def test_response_body_size_recorded(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {}, None)
        resp_body = b'{"id": "cmpl-1"}'
        rec.record_response(rid, 200, {}, resp_body, False, 10.0)
        rows = db_rows(rec, "raw_requests")
        assert rows[0]["response_body_size"] == len(resp_body)

    def test_stream_flag_stored(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {}, None)
        rec.record_response(rid, 200, {}, "data: [DONE]\n", True, 500.0)
        assert db_rows(rec, "raw_requests")[0]["is_stream"] == 1

    def test_error_stored(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {}, None)
        rec.record_response(rid, 502, {}, None, False, 10.0, error="connect failed")
        assert db_rows(rec, "raw_requests")[0]["error"] == "connect failed"

    def test_body_truncated_at_max_size(self, rec: Recorder):
        cfg = Config(log_dir=str(rec.log_dir), max_body_log_size=100)
        rec2 = Recorder(cfg)
        rid = rec2.new_request_id()
        big = b"x" * 200
        rec2.record_request(rid, "POST", "/v1/chat", "", {}, big)
        entries = jsonl_bodies(rec2)
        assert any("truncated" in e["data"] for e in entries)


class TestJSONLSharding:
    def test_jsonl_path_is_hourly(self, rec: Recorder):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        expected_name = now.strftime("%Y-%m-%d-%H") + ".jsonl"
        assert rec.jsonl_path.name == expected_name

    def test_bodies_dir_property(self, rec: Recorder):
        assert rec.bodies_dir == rec.log_dir / "bodies"


class TestRecordWS:
    def test_connect_creates_row(self, rec: Recorder):
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws/chat", "", {"origin": "test"}, "json")
        rows = db_rows(rec, "raw_ws_connections")
        assert len(rows) == 1
        assert rows[0]["path"] == "/ws/chat"
        assert rows[0]["subprotocol"] == "json"
        assert rows[0]["message_count"] == 0

    def test_ws_connect_client_ip_stored(self, rec: Recorder):
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws", "", {}, None,
                              client_ip="10.0.0.1", client_port=12345)
        rows = db_rows(rec, "raw_ws_connections")
        assert rows[0]["client_ip"] == "10.0.0.1"
        assert rows[0]["client_port"] == 12345

    def test_ws_seq_assigned(self, rec: Recorder):
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws", "", {}, None)
        rows = db_rows(rec, "raw_ws_connections")
        assert rows[0]["seq"] == 1

    def test_message_recorded_and_count_incremented(self, rec: Recorder):
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws", "", {}, None)
        rec.record_ws_message(cid, "client_to_server", "text", "hello", 10_000)
        rec.record_ws_message(cid, "server_to_client", "text", "world", 10_000)
        msgs = db_rows(rec, "raw_ws_messages")
        assert len(msgs) == 2
        assert msgs[0]["direction"] == "client_to_server"
        assert msgs[0]["data"] == "hello"
        conn_row = db_rows(rec, "raw_ws_connections")[0]
        assert conn_row["message_count"] == 2

    def test_message_data_size_recorded(self, rec: Recorder):
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws", "", {}, None)
        rec.record_ws_message(cid, "client_to_server", "text", "hello", 10_000)
        msgs = db_rows(rec, "raw_ws_messages")
        assert msgs[0]["data_size"] == len("hello".encode("utf-8"))

    def test_binary_message_stored_as_base64(self, rec: Recorder):
        import base64
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws", "", {}, None)
        rec.record_ws_message(cid, "server_to_client", "binary", b"\x00\x01\x02", 10_000)
        msgs = db_rows(rec, "raw_ws_messages")
        decoded = base64.b64decode(msgs[0]["data"])
        assert decoded == b"\x00\x01\x02"

    def test_close_sets_duration(self, rec: Recorder):
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws", "", {}, None)
        rec.record_ws_close(cid, 42.5)
        row = db_rows(rec, "raw_ws_connections")[0]
        assert abs(row["duration_ms"] - 42.5) < 0.01
        assert row["closed_at"] is not None

    def test_message_truncated_at_max_size(self, rec: Recorder):
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws", "", {}, None)
        long_msg = "a" * 200
        rec.record_ws_message(cid, "client_to_server", "text", long_msg, 50)
        msgs = db_rows(rec, "raw_ws_messages")
        data = msgs[0]["data"]
        assert "truncated" in data
        # The payload portion before the truncation marker must be ≤ max_size
        payload = data[:data.index("\n...")]
        assert len(payload) == 50
