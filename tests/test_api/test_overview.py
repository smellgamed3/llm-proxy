"""Tests for the Overview API endpoint."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from analyzer.store import AnalyticsStore


@pytest.fixture
def analytics_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "analytics.db")


@pytest.fixture
def store(analytics_db_path: str) -> AnalyticsStore:
    return AnalyticsStore(analytics_db_path)


@pytest.fixture
def client(analytics_db_path: str, tmp_path: Path, monkeypatch):
    raw_db = str(tmp_path / "raw.db")
    monkeypatch.setenv("ANALYTICS_DB", analytics_db_path)
    monkeypatch.setenv("RAW_DB", raw_db)
    monkeypatch.setenv("BODIES_DIR", str(tmp_path / "bodies"))
    # Initialize schema
    AnalyticsStore(analytics_db_path)
    app = create_app()
    return TestClient(app)


class TestOverviewEmpty:
    def test_empty_db_returns_zeros(self, client: TestClient):
        r = client.get("/api/overview")
        assert r.status_code == 200
        data = r.json()
        assert data["total_requests"] == 0
        assert data["success_count"] == 0
        assert data["error_count"] == 0
        assert data["success_rate"] == 0.0
        assert data["total_cost_usd"] == 0.0


class TestOverviewWithData:
    def test_overview_counts(self, client: TestClient, store: AnalyticsStore):
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        store.upsert_conversation({
            "id": "id-1", "seq": 1, "timestamp": ts,
            "status": "success", "cost_usd": 0.01,
            "duration_ms": 100.0, "total_tokens": 50,
        })
        store.upsert_conversation({
            "id": "id-2", "seq": 2, "timestamp": ts,
            "status": "error", "cost_usd": None,
            "duration_ms": 50.0, "total_tokens": None,
        })

        r = client.get("/api/overview")
        assert r.status_code == 200
        data = r.json()
        assert data["total_requests"] == 2
        assert data["success_count"] == 1
        assert data["error_count"] == 1
        assert data["success_rate"] == 0.5
        assert data["total_cost_usd"] > 0

    def test_daily_overview(self, client: TestClient, store: AnalyticsStore):
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        store.upsert_conversation({
            "id": "id-1", "seq": 1, "timestamp": ts,
            "status": "success", "cost_usd": 0.01,
            "duration_ms": 100.0, "total_tokens": 50,
        })
        store.refresh_daily_stats(ts[:10])

        r = client.get("/api/overview/daily?days=1")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
