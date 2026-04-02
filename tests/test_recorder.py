"""Tests for Recorder: SQLite schema, request/response recording, WS recording."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.config import Config
from app.recorder import Recorder
from tests.conftest import db_rows, jsonl_bodies


@pytest.fixture
def rec(tmp_path: Path) -> Recorder:
    cfg = Config(log_dir=str(tmp_path / "logs"))
    return Recorder(cfg)


class TestRecorderSchema:
    def test_tables_created(self, rec: Recorder):
        conn = sqlite3.connect(str(rec.db_path))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert {"requests", "ws_connections", "ws_messages"} <= tables

    def test_db_path_in_log_dir(self, rec: Recorder):
        assert rec.db_path.parent == rec.log_dir
        assert rec.db_path.exists()


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
        rows = db_rows(rec, "requests")
        assert len(rows) == 1
        r = rows[0]
        assert r["id"] == rid
        assert r["method"] == "POST"
        assert r["path"] == "/v1/chat/completions"
        assert r["model"] == "gpt-4o"

    def test_model_extracted_from_body(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {},
                           b'{"model": "claude-3-5-sonnet"}')
        rows = db_rows(rec, "requests")
        assert rows[0]["model"] == "claude-3-5-sonnet"

    def test_no_model_in_body(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "GET", "/v1/models", "", {}, None)
        rows = db_rows(rec, "requests")
        assert rows[0]["model"] is None

    def test_body_written_to_jsonl(self, rec: Recorder):
        rid = rec.new_request_id()
        body = b'{"model": "gpt-4", "messages": []}'
        rec.record_request(rid, "POST", "/v1/chat", "", {}, body)
        entries = jsonl_bodies(rec)
        assert any(rid in e["ref"] and "gpt-4" in e["data"] for e in entries)

    def test_no_body_no_jsonl_entry(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "GET", "/v1/models", "", {}, None)
        entries = jsonl_bodies(rec)
        assert not any(rid in e["ref"] for e in entries)


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
        rows = db_rows(rec, "requests")
        assert rows[0]["status_code"] == 200
        assert abs(rows[0]["duration_ms"] - 123.4) < 0.01
        assert rows[0]["is_stream"] == 0

    def test_stream_flag_stored(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {}, None)
        rec.record_response(rid, 200, {}, "data: [DONE]\n", True, 500.0)
        assert db_rows(rec, "requests")[0]["is_stream"] == 1

    def test_error_stored(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {}, None)
        rec.record_response(rid, 502, {}, None, False, 10.0, error="connect failed")
        assert db_rows(rec, "requests")[0]["error"] == "connect failed"

    def test_body_truncated_at_max_size(self, rec: Recorder):
        cfg = Config(log_dir=str(rec.log_dir), max_body_log_size=100)
        rec2 = Recorder(cfg)
        rid = rec2.new_request_id()
        big = b"x" * 200
        rec2.record_request(rid, "POST", "/v1/chat", "", {}, big)
        entries = jsonl_bodies(rec2)
        assert any("truncated" in e["data"] for e in entries)


class TestRecordWS:
    def test_connect_creates_row(self, rec: Recorder):
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws/chat", "", {"origin": "test"}, "json")
        rows = db_rows(rec, "ws_connections")
        assert len(rows) == 1
        assert rows[0]["path"] == "/ws/chat"
        assert rows[0]["subprotocol"] == "json"
        assert rows[0]["message_count"] == 0

    def test_message_recorded_and_count_incremented(self, rec: Recorder):
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws", "", {}, None)
        rec.record_ws_message(cid, "client_to_server", "text", "hello", 10_000)
        rec.record_ws_message(cid, "server_to_client", "text", "world", 10_000)
        msgs = db_rows(rec, "ws_messages")
        assert len(msgs) == 2
        assert msgs[0]["direction"] == "client_to_server"
        assert msgs[0]["data"] == "hello"
        conn_row = db_rows(rec, "ws_connections")[0]
        assert conn_row["message_count"] == 2

    def test_binary_message_stored_as_base64(self, rec: Recorder):
        import base64
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws", "", {}, None)
        rec.record_ws_message(cid, "server_to_client", "binary", b"\x00\x01\x02", 10_000)
        msgs = db_rows(rec, "ws_messages")
        decoded = base64.b64decode(msgs[0]["data"])
        assert decoded == b"\x00\x01\x02"

    def test_close_sets_duration(self, rec: Recorder):
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws", "", {}, None)
        rec.record_ws_close(cid, 42.5)
        row = db_rows(rec, "ws_connections")[0]
        assert abs(row["duration_ms"] - 42.5) < 0.01
        assert row["closed_at"] is not None

    def test_message_truncated_at_max_size(self, rec: Recorder):
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws", "", {}, None)
        long_msg = "a" * 200
        rec.record_ws_message(cid, "client_to_server", "text", long_msg, 50)
        msgs = db_rows(rec, "ws_messages")
        data = msgs[0]["data"]
        assert "truncated" in data
        # The payload portion before the truncation marker must be ≤ max_size
        payload = data[:data.index("\n...")]
        assert len(payload) == 50
