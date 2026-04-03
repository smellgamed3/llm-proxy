"""Tests for the Conversations API endpoints."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from analyzer.store import AnalyticsStore


@pytest.fixture
def analytics_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "analytics.db")


@pytest.fixture
def raw_db_path(tmp_path: Path) -> str:
    # Create a minimal raw.db
    path = str(tmp_path / "raw.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE raw_requests (
            id TEXT PRIMARY KEY,
            seq INTEGER,
            timestamp TEXT,
            method TEXT,
            path TEXT,
            status_code INTEGER,
            duration_ms REAL
        );
    """)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def store(analytics_db_path: str) -> AnalyticsStore:
    return AnalyticsStore(analytics_db_path)


@pytest.fixture
def client(analytics_db_path: str, raw_db_path: str, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANALYTICS_DB", analytics_db_path)
    monkeypatch.setenv("RAW_DB", raw_db_path)
    monkeypatch.setenv("BODIES_DIR", str(tmp_path / "bodies"))
    # Initialize analytics schema
    AnalyticsStore(analytics_db_path)
    return TestClient(create_app())


def _make_conv(idx: int, model: str = "gpt-4o", status: str = "success") -> dict:
    ts = f"2024-01-0{idx}T10:00:00Z"
    return {
        "id": f"conv-{idx}",
        "seq": idx,
        "timestamp": ts,
        "model": model,
        "status": status,
        "cost_usd": 0.01 * idx,
        "duration_ms": 100.0 * idx,
        "total_tokens": 10 * idx,
    }


class TestListConversations:
    def test_empty_returns_empty(self, client: TestClient):
        r = client.get("/api/conversations")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_lists_conversations(self, client: TestClient, store: AnalyticsStore):
        for i in range(1, 4):
            store.upsert_conversation(_make_conv(i))

        r = client.get("/api/conversations")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3

    def test_filter_by_model(self, client: TestClient, store: AnalyticsStore):
        store.upsert_conversation(_make_conv(1, model="gpt-4o"))
        store.upsert_conversation(_make_conv(2, model="gpt-3.5-turbo"))

        r = client.get("/api/conversations?model=gpt-4o")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["items"][0]["model"] == "gpt-4o"

    def test_filter_by_status(self, client: TestClient, store: AnalyticsStore):
        store.upsert_conversation(_make_conv(1, status="success"))
        store.upsert_conversation(_make_conv(2, status="error"))

        r = client.get("/api/conversations?status=error")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "error"

    def test_pagination(self, client: TestClient, store: AnalyticsStore):
        for i in range(1, 11):
            store.upsert_conversation(_make_conv(i))

        r = client.get("/api/conversations?page=1&page_size=5")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 10
        assert len(data["items"]) == 5

        r2 = client.get("/api/conversations?page=2&page_size=5")
        assert r2.status_code == 200
        data2 = r2.json()
        assert len(data2["items"]) == 5

        # All IDs should be different across pages
        ids1 = {item["id"] for item in data["items"]}
        ids2 = {item["id"] for item in data2["items"]}
        assert ids1.isdisjoint(ids2)

    def test_date_range_filter(self, client: TestClient, store: AnalyticsStore):
        store.upsert_conversation(_make_conv(1))  # 2024-01-01
        store.upsert_conversation(_make_conv(2))  # 2024-01-02
        store.upsert_conversation(_make_conv(3))  # 2024-01-03

        r = client.get("/api/conversations?date_from=2024-01-02&date_to=2024-01-02")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1


class TestGetConversation:
    def test_get_existing(self, client: TestClient, store: AnalyticsStore):
        store.upsert_conversation(_make_conv(1))
        r = client.get("/api/conversations/conv-1")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == "conv-1"

    def test_get_nonexistent_returns_404(self, client: TestClient):
        r = client.get("/api/conversations/nonexistent")
        assert r.status_code == 404

    def test_get_raw_nonexistent_returns_404(self, client: TestClient):
        r = client.get("/api/conversations/nonexistent/raw")
        assert r.status_code == 404
