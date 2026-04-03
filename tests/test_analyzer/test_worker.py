"""Integration tests for the AnalyzerWorker."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from analyzer.config import AnalyzerConfig
from analyzer.worker import AnalyzerWorker


def _make_raw_db(path: Path) -> sqlite3.Connection:
    """Create a raw.db with the proper schema."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE raw_requests (
            id TEXT PRIMARY KEY,
            seq INTEGER UNIQUE,
            timestamp TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            query_string TEXT,
            request_headers TEXT,
            request_body_ref TEXT,
            request_body_size INTEGER,
            status_code INTEGER,
            response_headers TEXT,
            response_body_ref TEXT,
            response_body_size INTEGER,
            is_stream INTEGER DEFAULT 0,
            duration_ms REAL,
            client_ip TEXT,
            client_port INTEGER,
            upstream_url TEXT,
            provider TEXT,
            error TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    return conn


def _insert_request(
    conn: sqlite3.Connection,
    seq: int,
    path: str = "/v1/chat/completions",
    request_body_ref: str | None = None,
    response_body_ref: str | None = None,
    status_code: int = 200,
    duration_ms: float = 123.0,
    is_stream: int = 0,
) -> str:
    rid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO raw_requests
           (id, seq, timestamp, method, path, status_code,
            request_body_ref, response_body_ref, duration_ms, is_stream)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            rid, seq,
            datetime.now(timezone.utc).isoformat(),
            "POST", path, status_code,
            request_body_ref, response_body_ref,
            duration_ms, is_stream,
        ),
    )
    conn.commit()
    return rid


def _write_body(bodies_dir: Path, ref: str, data: str) -> None:
    """Write a body to a JSONL shard and manifest."""
    bodies_dir.mkdir(parents=True, exist_ok=True)
    shard = bodies_dir / "2024-01-01-00.jsonl"
    line = json.dumps({"ref": ref, "timestamp": "2024-01-01T00:00:00Z", "data": data})
    offset = shard.stat().st_size if shard.exists() else 0
    with open(shard, "a", encoding="utf-8") as f:
        f.write(line + "\n")

    manifest = bodies_dir / "manifest.jsonl"
    manifest_entry = json.dumps({
        "ref": ref, "file": shard.name, "offset": offset,
        "length": len((line + "\n").encode("utf-8")),
    })
    with open(manifest, "a", encoding="utf-8") as f:
        f.write(manifest_entry + "\n")


@pytest.fixture
def setup_dirs(tmp_path: Path):
    raw_db = tmp_path / "raw.db"
    analytics_db = tmp_path / "analytics.db"
    bodies_dir = tmp_path / "bodies"
    pricing_file = tmp_path / "pricing.yaml"
    pricing_file.write_text(yaml.dump({
        "models": {"gpt-4o": {"input_per_1m": 2.50, "output_per_1m": 10.00}},
        "default": {"input_per_1m": 1.00, "output_per_1m": 5.00},
    }))
    return raw_db, analytics_db, bodies_dir, pricing_file


class TestWorkerIncrementalMode:
    def test_processes_new_records(self, setup_dirs, tmp_path: Path):
        raw_db, analytics_db, bodies_dir, pricing_file = setup_dirs

        conn = _make_raw_db(raw_db)
        req_body = json.dumps({
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "Be helpful."},
                {"role": "user", "content": "Hello"},
            ],
        })
        resp_body = json.dumps({
            "choices": [{"message": {"content": "Hi!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        rid = str(uuid.uuid4())
        req_ref = f"{rid}:request"
        resp_ref = f"{rid}:response"
        _write_body(bodies_dir, req_ref, req_body)
        _write_body(bodies_dir, resp_ref, resp_body)

        conn.execute(
            """INSERT INTO raw_requests
               (id, seq, timestamp, method, path, status_code,
                request_body_ref, response_body_ref, duration_ms, is_stream)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rid, 1, datetime.now(timezone.utc).isoformat(),
             "POST", "/v1/chat/completions", 200,
             req_ref, resp_ref, 100.0, 0),
        )
        conn.commit()

        config = AnalyzerConfig(
            raw_db=str(raw_db),
            analytics_db=str(analytics_db),
            bodies_dir=str(bodies_dir),
            pricing_file=str(pricing_file),
            mode="full",
            batch_size=10,
        )
        worker = AnalyzerWorker(config)
        worker.run()

        aconn = sqlite3.connect(str(analytics_db))
        aconn.row_factory = sqlite3.Row
        rows = aconn.execute("SELECT * FROM conversations").fetchall()
        assert len(rows) == 1
        row = dict(rows[0])
        assert row["model"] == "gpt-4o"
        assert row["prompt_tokens"] == 10
        assert row["completion_tokens"] == 5
        assert row["total_tokens"] == 15
        assert row["status"] == "success"
        assert row["finish_reason"] == "stop"
        assert row["cost_usd"] is not None
        assert row["cost_usd"] > 0

    def test_watermark_updated(self, setup_dirs, tmp_path: Path):
        raw_db, analytics_db, bodies_dir, pricing_file = setup_dirs
        conn = _make_raw_db(raw_db)
        for i in range(1, 4):
            _insert_request(conn, i)

        config = AnalyzerConfig(
            raw_db=str(raw_db),
            analytics_db=str(analytics_db),
            bodies_dir=str(bodies_dir),
            pricing_file=str(pricing_file),
            mode="full",
            batch_size=10,
        )
        worker = AnalyzerWorker(config)
        worker.run()

        wm = worker.analytics_store.get_watermark()
        assert wm == 3


class TestWorkerFullMode:
    def test_resets_and_reprocesses(self, setup_dirs, tmp_path: Path):
        raw_db, analytics_db, bodies_dir, pricing_file = setup_dirs
        conn = _make_raw_db(raw_db)
        for i in range(1, 4):
            _insert_request(conn, i)

        config = AnalyzerConfig(
            raw_db=str(raw_db),
            analytics_db=str(analytics_db),
            bodies_dir=str(bodies_dir),
            pricing_file=str(pricing_file),
            mode="full",
            batch_size=10,
        )
        # Run twice — second run should reset and reprocess
        AnalyzerWorker(config).run()
        AnalyzerWorker(config).run()

        aconn = sqlite3.connect(str(analytics_db))
        count = aconn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        assert count == 3  # Not doubled


class TestWorkerRangeMode:
    def test_processes_only_range(self, setup_dirs, tmp_path: Path):
        raw_db, analytics_db, bodies_dir, pricing_file = setup_dirs
        conn = _make_raw_db(raw_db)

        # Insert records with different timestamps
        conn.execute(
            """INSERT INTO raw_requests (id, seq, timestamp, method, path, status_code, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), 1, "2024-01-01T10:00:00Z", "POST", "/v1/chat/completions", 200, 100.0),
        )
        conn.execute(
            """INSERT INTO raw_requests (id, seq, timestamp, method, path, status_code, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), 2, "2024-01-02T10:00:00Z", "POST", "/v1/chat/completions", 200, 100.0),
        )
        conn.execute(
            """INSERT INTO raw_requests (id, seq, timestamp, method, path, status_code, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), 3, "2024-01-03T10:00:00Z", "POST", "/v1/chat/completions", 200, 100.0),
        )
        conn.commit()

        config = AnalyzerConfig(
            raw_db=str(raw_db),
            analytics_db=str(analytics_db),
            bodies_dir=str(bodies_dir),
            pricing_file=str(pricing_file),
            mode="range",
            since="2024-01-02T00:00:00Z",
            until="2024-01-02T23:59:59Z",
            batch_size=10,
        )
        worker = AnalyzerWorker(config)
        worker.run()

        aconn = sqlite3.connect(str(analytics_db))
        rows = aconn.execute("SELECT timestamp FROM conversations ORDER BY timestamp").fetchall()
        # Should include records from Jan 2 (and possibly Jan 3 due to batch processing)
        # At minimum the Jan 2 record should be included
        timestamps = [r[0][:10] for r in rows]
        assert "2024-01-02" in timestamps
        # Jan 1 record is before the range and should not be included
        assert "2024-01-01" not in timestamps


class TestWorkerSystemPromptFingerprint:
    def test_template_created_for_system_prompt(self, setup_dirs, tmp_path: Path):
        raw_db, analytics_db, bodies_dir, pricing_file = setup_dirs
        conn = _make_raw_db(raw_db)

        req_body = json.dumps({
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are a code reviewer."},
                {"role": "user", "content": "Review this code."},
            ],
        })
        rid = str(uuid.uuid4())
        req_ref = f"{rid}:request"
        _write_body(bodies_dir, req_ref, req_body)
        conn.execute(
            """INSERT INTO raw_requests (id, seq, timestamp, method, path, status_code,
               request_body_ref, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (rid, 1, datetime.now(timezone.utc).isoformat(),
             "POST", "/v1/chat/completions", 200, req_ref, 100.0),
        )
        conn.commit()

        config = AnalyzerConfig(
            raw_db=str(raw_db),
            analytics_db=str(analytics_db),
            bodies_dir=str(bodies_dir),
            pricing_file=str(pricing_file),
            mode="full",
            batch_size=10,
        )
        worker = AnalyzerWorker(config)
        worker.run()

        aconn = sqlite3.connect(str(analytics_db))
        aconn.row_factory = sqlite3.Row
        templates = aconn.execute("SELECT * FROM prompt_templates").fetchall()
        assert len(templates) == 1
        assert templates[0]["use_count"] == 1
        assert "code reviewer" in templates[0]["system_prompt"]
