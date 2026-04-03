"""Tests for Recorder: SQLite schema, request/response recording, WS recording."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from app.config import Config
from app.recorder import Recorder, SeqGenerator
from tests.conftest import db_rows, jsonl_bodies


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
        manifest = rec.bodies_dir / "manifest.jsonl"
        assert manifest.exists()
        entries = [json.loads(l) for l in manifest.read_text().splitlines() if l.strip()]
        assert len(entries) == 1
        assert entries[0]["ref"].startswith(rid)
        assert "file" in entries[0]
        assert "offset" in entries[0]
        assert "length" in entries[0]


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
