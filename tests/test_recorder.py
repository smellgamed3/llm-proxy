"""Tests for Recorder: SQLite schema, request/response recording, WS recording."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.config import Config
from app.recorder import Recorder, SeqGenerator
from tests.conftest import db_rows, jsonl_bodies, jsonl_manifest


@pytest.fixture
def rec(tmp_path: Path) -> Recorder:
    cfg = Config(log_dir=str(tmp_path / "logs"))
    return Recorder(cfg)


class TestSeqGenerator:
    def test_starts_at_one(self, tmp_path: Path):
        cfg = Config(log_dir=str(tmp_path / "logs"))
        rec = Recorder(cfg)
        assert rec._http_seq.next() == 1
        assert rec._http_seq.next() == 2

    def test_monotonically_increasing(self, tmp_path: Path):
        cfg = Config(log_dir=str(tmp_path / "logs"))
        rec = Recorder(cfg)
        vals = [rec._http_seq.next() for _ in range(10)]
        assert vals == list(range(1, 11))

    def test_resumes_from_max_seq_after_restart(self, tmp_path: Path):
        cfg = Config(log_dir=str(tmp_path / "logs"))
        rec1 = Recorder(cfg)
        rec1.record_request(rec1.new_request_id(), "GET", "/a", "", {}, None)
        rec1.record_request(rec1.new_request_id(), "GET", "/b", "", {}, None)
        # Second recorder reloads from DB
        rec2 = Recorder(cfg)
        assert rec2._http_seq.next() == 3


class TestRecorderSchema:
    def test_tables_created(self, rec: Recorder):
        conn = sqlite3.connect(str(rec.db_path))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert {"raw_requests", "raw_ws_connections", "raw_ws_messages"} <= tables

    def test_db_named_raw(self, rec: Recorder):
        assert rec.db_path.name == "raw.db"

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

    def test_seq_assigned(self, rec: Recorder):
        rid1 = rec.new_request_id()
        rid2 = rec.new_request_id()
        rec.record_request(rid1, "GET", "/a", "", {}, None)
        rec.record_request(rid2, "GET", "/b", "", {}, None)
        rows = db_rows(rec, "raw_requests")
        seqs = sorted(r["seq"] for r in rows)
        assert seqs[1] == seqs[0] + 1

    def test_no_model_in_raw_requests(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {},
                           b'{"model": "gpt-4o", "messages": []}')
        rows = db_rows(rec, "raw_requests")
        assert "model" not in rows[0]

    def test_client_ip_and_port_stored(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {}, None,
                           client_ip="10.0.0.1", client_port=54321)
        rows = db_rows(rec, "raw_requests")
        assert rows[0]["client_ip"] == "10.0.0.1"
        assert rows[0]["client_port"] == 54321

    def test_upstream_url_stored(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {}, None,
                           upstream_url="http://llm.internal:8080/v1/chat")
        rows = db_rows(rec, "raw_requests")
        assert rows[0]["upstream_url"] == "http://llm.internal:8080/v1/chat"

    def test_request_body_size_stored(self, rec: Recorder):
        rid = rec.new_request_id()
        body = b'{"model": "gpt-4"}' 
        rec.record_request(rid, "POST", "/v1/chat", "", {}, body)
        rows = db_rows(rec, "raw_requests")
        assert rows[0]["request_body_size"] == len(body)

    def test_no_body_no_size(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "GET", "/v1/models", "", {}, None)
        rows = db_rows(rec, "raw_requests")
        assert rows[0]["request_body_size"] is None

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

    def test_manifest_written(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {}, b'hello')
        manifest = jsonl_manifest(rec)
        assert any(rid in m["ref"] for m in manifest)

    def test_manifest_entry_has_required_fields(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {}, b'hello')
        manifest = jsonl_manifest(rec)
        entry = next(m for m in manifest if rid in m["ref"])
        assert "file" in entry
        assert "offset" in entry
        assert "length" in entry
        assert entry["offset"] >= 0
        assert entry["length"] > 0

    def test_jsonl_shard_filename_contains_hour(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {}, b'body')
        shards = [f for f in rec.bodies_dir.glob("*.jsonl") if f.name != "manifest.jsonl"]
        assert len(shards) == 1
        # filename pattern: YYYY-MM-DD-HH.jsonl
        parts = shards[0].stem.split("-")
        assert len(parts) == 4


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

    def test_response_body_size_stored(self, rec: Recorder):
        rid = rec.new_request_id()
        rec.record_request(rid, "POST", "/v1/chat", "", {}, None)
        body = b'{"id": "chatcmpl-1"}'
        rec.record_response(rid, 200, {}, body, False, 50.0)
        rows = db_rows(rec, "raw_requests")
        assert rows[0]["response_body_size"] == len(body)

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


class TestRecordWS:
    def test_connect_creates_row(self, rec: Recorder):
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws/chat", "", {"origin": "test"}, "json")
        rows = db_rows(rec, "raw_ws_connections")
        assert len(rows) == 1
        assert rows[0]["path"] == "/ws/chat"
        assert rows[0]["subprotocol"] == "json"
        assert rows[0]["message_count"] == 0

    def test_connect_seq_assigned(self, rec: Recorder):
        cid1, cid2 = rec.new_request_id(), rec.new_request_id()
        rec.record_ws_connect(cid1, "/ws", "", {}, None)
        rec.record_ws_connect(cid2, "/ws", "", {}, None)
        rows = db_rows(rec, "raw_ws_connections")
        seqs = sorted(r["seq"] for r in rows)
        assert seqs[1] == seqs[0] + 1

    def test_connect_client_ip_port_stored(self, rec: Recorder):
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws", "", {}, None,
                              client_ip="192.168.1.1", client_port=12345)
        rows = db_rows(rec, "raw_ws_connections")
        assert rows[0]["client_ip"] == "192.168.1.1"
        assert rows[0]["client_port"] == 12345

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

    def test_message_data_size_stored(self, rec: Recorder):
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws", "", {}, None)
        msg = "hello world"
        rec.record_ws_message(cid, "client_to_server", "text", msg, 10_000)
        msgs = db_rows(rec, "raw_ws_messages")
        assert msgs[0]["data_size"] == len(msg.encode("utf-8"))

    def test_binary_message_stored_as_base64(self, rec: Recorder):
        import base64
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws", "", {}, None)
        rec.record_ws_message(cid, "server_to_client", "binary", b"\x00\x01\x02", 10_000)
        msgs = db_rows(rec, "raw_ws_messages")
        decoded = base64.b64decode(msgs[0]["data"])
        assert decoded == b"\x00\x01\x02"

    def test_binary_message_data_size_is_original_bytes(self, rec: Recorder):
        cid = rec.new_request_id()
        rec.record_ws_connect(cid, "/ws", "", {}, None)
        payload = b"\xff" * 50
        rec.record_ws_message(cid, "server_to_client", "binary", payload, 10_000)
        msgs = db_rows(rec, "raw_ws_messages")
        assert msgs[0]["data_size"] == 50

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
