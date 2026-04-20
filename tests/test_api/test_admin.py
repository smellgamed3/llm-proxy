"""Tests for admin analytics endpoints."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from analyzer.store import AnalyticsStore
from analyzer.worker import AnalyzerWorker
from tests.test_api.conftest import ADMIN_HEADERS


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    analytics_db = tmp_path / "analytics.db"
    raw_db = tmp_path / "raw.db"
    bodies_dir = tmp_path / "bodies"
    pricing = tmp_path / "pricing.yaml"

    monkeypatch.setenv("ANALYTICS_DB", str(analytics_db))
    monkeypatch.setenv("RAW_DB", str(raw_db))
    monkeypatch.setenv("BODIES_DIR", str(bodies_dir))
    monkeypatch.setenv("PRICING_FILE", str(pricing))

    pricing.write_text(
        json.dumps({
            "models": {"gpt-4o": {"input_per_1m": 2.5, "output_per_1m": 10.0}},
            "default": {"input_per_1m": 1.0, "output_per_1m": 5.0},
        }),
        encoding="utf-8",
    )
    AnalyticsStore(str(analytics_db))

    conn = sqlite3.connect(raw_db)
    conn.executescript(
        """
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
        """
    )
    conn.execute(
        """INSERT INTO raw_requests (id, seq, timestamp, method, path, status_code, duration_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("raw-1", 1, "2024-01-01T00:00:00Z", "POST", "/v1/chat/completions", 200, 15.0),
    )
    conn.commit()
    conn.close()

    return TestClient(create_app(), headers=ADMIN_HEADERS)

def test_admin_status_alias(client: TestClient):
    response = client.get("/api/admin/analyzer/status")
    assert response.status_code == 200
    assert "watermark_seq" in response.json()
    assert "raw_db" in response.json()
    assert "analytics_db" in response.json()
    assert "worker" in response.json()


def test_admin_status_at_legacy_path(client: TestClient):
    response = client.get("/api/admin/status")
    assert response.status_code == 200
    data = response.json()
    assert "watermark_seq" in data
    assert "conversation_count" in data
    assert "template_count" in data


def test_admin_list_raw_requests(client: TestClient):
    response = client.get("/api/admin/raw-requests")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == "raw-1"


def test_admin_get_raw_request_with_rehydrated_bodies(
    client: TestClient,
):
    bodies_dir = Path(os.environ["BODIES_DIR"])
    bodies_dir.mkdir(parents=True, exist_ok=True)
    shard_path = bodies_dir / "2024-01-01-00.jsonl"
    request_ref = "raw-1:request"
    response_ref = "raw-1:response"

    request_line = json.dumps({"ref": request_ref, "timestamp": "2024-01-01T00:00:00Z", "data": "request body"}) + "\n"
    response_line = json.dumps({"ref": response_ref, "timestamp": "2024-01-01T00:00:01Z", "data": "response body"}) + "\n"
    with open(shard_path, "wb") as handle:
        handle.write(request_line.encode("utf-8"))
        request_offset = 0
        response_offset = handle.tell()
        handle.write(response_line.encode("utf-8"))

    (bodies_dir / "manifest.jsonl").write_text(
        json.dumps({"ref": request_ref, "file": shard_path.name, "offset": request_offset, "length": len(request_line.encode("utf-8"))}) + "\n"
        + json.dumps({"ref": response_ref, "file": shard_path.name, "offset": response_offset, "length": len(response_line.encode("utf-8"))}) + "\n",
        encoding="utf-8",
    )

    raw_db = Path(os.environ["RAW_DB"])
    conn = sqlite3.connect(raw_db)
    conn.execute(
        "UPDATE raw_requests SET request_body_ref = ?, response_body_ref = ?, request_headers = ?, response_headers = ? WHERE id = ?",
        (request_ref, response_ref, json.dumps({"content-type": "application/json"}), json.dumps({"x-upstream": "mock"}), "raw-1"),
    )
    conn.commit()
    conn.close()

    response = client.get("/api/admin/raw-requests/raw-1")
    assert response.status_code == 200
    data = response.json()
    assert data["request_body"] == "request body"
    assert data["response_body"] == "response body"
    assert json.loads(data["request_headers"])["content-type"] == "application/json"


def test_admin_rerun_incremental(client: TestClient):
    response = client.post("/api/admin/analyzer/rerun", json={"mode": "incremental"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["mode"] == "incremental"
    assert data["processed"] == 1


def test_admin_start_background_sync(client: TestClient):
    response = client.post("/api/admin/analyzer/sync", json={"mode": "incremental"})
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "started"
    assert data["job"]["mode"] == "incremental"

    job = client.get("/api/admin/analyzer/job")
    assert job.status_code == 200
    assert "status" in job.json()

    history = client.get("/api/admin/analyzer/history")
    assert history.status_code == 200
    assert isinstance(history.json(), list)
    assert history.json()[0]["mode"] == "incremental"


def test_admin_stop_background_sync(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    # Force single-process so the _process_record monkey-patch takes effect.
    # Stop-signal checking happens in the main process between batches regardless
    # of num_workers; this test specifically exercises the stop-flag path.
    monkeypatch.setenv("ANALYZER_NUM_WORKERS", "1")
    raw_db = os.environ["RAW_DB"]
    conn = sqlite3.connect(raw_db)
    for idx in range(2, 10):
        conn.execute(
            """INSERT INTO raw_requests (id, seq, timestamp, method, path, status_code, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (f"raw-{idx}", idx, f"2024-01-01T00:00:{idx:02d}Z", "POST", "/v1/chat/completions", 200, 15.0),
        )
    conn.commit()
    conn.close()

    original_process_record = AnalyzerWorker._process_record

    def slow_process_record(self, record, dates_to_refresh):
        time.sleep(0.03)
        return original_process_record(self, record, dates_to_refresh)

    monkeypatch.setattr(AnalyzerWorker, "_process_record", slow_process_record)

    response = client.post("/api/admin/analyzer/sync", json={"mode": "incremental"})
    assert response.status_code == 202

    stop_response = client.post("/api/admin/analyzer/stop")
    assert stop_response.status_code == 200
    assert stop_response.json()["status"] == "stopping"

    deadline = time.time() + 3
    final_job = None
    while time.time() < deadline:
      final_job = client.get("/api/admin/analyzer/job")
      data = final_job.json()
      if data["status"] in {"stopped", "completed", "failed"}:
          break
      time.sleep(0.05)

    assert final_job is not None
    data = final_job.json()
    assert data["status"] == "stopped"
    assert data["stop_requested"] is True

    history = client.get("/api/admin/analyzer/history").json()
    assert history[0]["status"] == "stopped"
    assert history[0]["stop_requested"] == 1


def test_admin_rerun_full(client: TestClient):
    response = client.post("/api/admin/analyzer/rerun", json={"mode": "full"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["mode"] == "full"
    assert data["processed"] == 1


def test_admin_reset(client: TestClient):
    # First run incremental to populate some data
    client.post("/api/admin/analyzer/rerun", json={"mode": "incremental"})
    status_before = client.get("/api/admin/analyzer/status").json()
    assert status_before["conversation_count"] >= 1

    response = client.post("/api/admin/reset")
    assert response.status_code == 200
    assert "reset" in response.json()["status"]

    status_after = client.get("/api/admin/analyzer/status").json()
    assert status_after["conversation_count"] == 0


def test_admin_retry_sync_job(client: TestClient):
    # Start a sync so we have a job in history
    response = client.post("/api/admin/analyzer/sync", json={"mode": "incremental"})
    assert response.status_code == 202
    job_id = response.json()["job"]["job_id"]

    # Wait for it to finish
    deadline = time.time() + 5
    while time.time() < deadline:
        job = client.get("/api/admin/analyzer/job").json()
        if not job.get("is_running"):
            break
        time.sleep(0.05)

    # Retry the completed job
    retry_resp = client.post(f"/api/admin/analyzer/retry/{job_id}")
    assert retry_resp.status_code == 202
    data = retry_resp.json()
    assert data["status"] == "started"
    assert data["job"]["mode"] == "incremental"
    # Should be a new job id
    assert data["job"]["job_id"] != job_id


def test_admin_retry_unknown_job(client: TestClient):
    resp = client.post("/api/admin/analyzer/retry/99999")
    assert resp.status_code == 404


def test_admin_backup_and_list(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    backup_dir = tmp_path / "backups"
    monkeypatch.setenv("BACKUP_DIR", str(backup_dir))

    # Source files don't exist in tmp_path by default for analytics/raw; backup should skip them gracefully
    resp = client.post("/api/admin/backup")
    assert resp.status_code == 200
    data = resp.json()
    assert "timestamp" in data
    assert isinstance(data["files"], list)

    # List backups — should return a list (may be empty if no source files existed)
    list_resp = client.get("/api/admin/backups")
    assert list_resp.status_code == 200
    assert isinstance(list_resp.json(), list)


def test_admin_backup_creates_files(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When analytics.db exists, backup creates a copy in BACKUP_DIR."""
    analytics_db = Path(os.environ["ANALYTICS_DB"])
    backup_dir = tmp_path / "backups"
    monkeypatch.setenv("BACKUP_DIR", str(backup_dir))

    resp = client.post("/api/admin/backup")
    assert resp.status_code == 200
    data = resp.json()
    # analytics.db should be backed up (raw.db may not exist in this fixture)
    assert any("analytics" in f["name"] for f in data["files"])

    # File should exist on disk
    ts = data["timestamp"]
    backup_file = backup_dir / f"analytics_{ts}.db"
    assert backup_file.exists()