"""Tests for admin analytics endpoints."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from analyzer.store import AnalyticsStore


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

    return TestClient(create_app())


def test_admin_status_alias(client: TestClient):
    response = client.get("/api/admin/analyzer/status")
    assert response.status_code == 200
    assert "watermark_seq" in response.json()


def test_admin_rerun_incremental(client: TestClient):
    response = client.post("/api/admin/analyzer/rerun", json={"mode": "incremental"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["mode"] == "incremental"
    assert data["processed"] == 1