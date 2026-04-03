"""Tests for error analytics API endpoints."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from analyzer.store import AnalyticsStore


@pytest.fixture
def store_and_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[AnalyticsStore, TestClient]:
    analytics_db = tmp_path / "analytics.db"
    monkeypatch.setenv("ANALYTICS_DB", str(analytics_db))
    monkeypatch.setenv("RAW_DB", str(tmp_path / "raw.db"))
    monkeypatch.setenv("BODIES_DIR", str(tmp_path / "bodies"))
    store = AnalyticsStore(str(analytics_db))
    return store, TestClient(create_app())


def _insert_conv(store: AnalyticsStore, conv_id: str, seq: int, timestamp: str,
                 status: str, error_type: str | None = None) -> None:
    with store._get_conn() as conn:
        conn.execute(
            """INSERT INTO conversations (id, seq, timestamp, status, error_type)
               VALUES (?, ?, ?, ?, ?)""",
            (conv_id, seq, timestamp, status, error_type),
        )


class TestErrorsSummary:
    def test_empty_returns_zeros(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        _, client = store_and_client
        r = client.get("/api/errors/summary")
        assert r.status_code == 200
        data = r.json()
        assert data["total_requests"] == 0
        assert data["error_count"] == 0
        assert data["error_rate"] == 0.0
        assert data["top_error_types"] == []

    def test_counts_errors_correctly(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _insert_conv(store, "c1", 1, "2024-01-01T10:00:00Z", "success")
        _insert_conv(store, "c2", 2, "2024-01-01T11:00:00Z", "error", "rate_limit")
        _insert_conv(store, "c3", 3, "2024-01-01T12:00:00Z", "error", "timeout")
        r = client.get("/api/errors/summary")
        assert r.status_code == 200
        data = r.json()
        assert data["total_requests"] == 3
        assert data["error_count"] == 2
        assert abs(data["error_rate"] - 0.6667) < 1e-3

    def test_error_rate_zero_when_all_success(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        for i in range(1, 4):
            _insert_conv(store, f"c{i}", i, "2024-01-01T10:00:00Z", "success")
        r = client.get("/api/errors/summary")
        data = r.json()
        assert data["error_rate"] == 0.0

    def test_top_error_types_ranked_by_count(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        for i in range(3):
            _insert_conv(store, f"rl-{i}", i + 1, "2024-01-01T10:00:00Z", "error", "rate_limit")
        _insert_conv(store, "to-1", 4, "2024-01-01T10:00:00Z", "error", "timeout")
        r = client.get("/api/errors/summary")
        top = r.json()["top_error_types"]
        assert top[0]["error_type"] == "rate_limit"
        assert top[0]["count"] == 3

    def test_date_from_filter(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _insert_conv(store, "c1", 1, "2024-01-01T10:00:00Z", "error", "timeout")
        _insert_conv(store, "c2", 2, "2024-01-03T10:00:00Z", "error", "timeout")
        r = client.get("/api/errors/summary?date_from=2024-01-02")
        data = r.json()
        assert data["total_requests"] == 1

    def test_date_to_filter(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _insert_conv(store, "c1", 1, "2024-01-01T10:00:00Z", "error", "timeout")
        _insert_conv(store, "c2", 2, "2024-01-03T10:00:00Z", "error", "timeout")
        r = client.get("/api/errors/summary?date_to=2024-01-02")
        data = r.json()
        assert data["total_requests"] == 1

    def test_date_range_filter(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _insert_conv(store, "c1", 1, "2024-01-01T10:00:00Z", "error", "timeout")
        _insert_conv(store, "c2", 2, "2024-01-05T10:00:00Z", "error", "timeout")
        r = client.get("/api/errors/summary?date_from=2024-01-01&date_to=2024-01-03")
        data = r.json()
        assert data["total_requests"] == 1


class TestRecentErrors:
    def test_empty_returns_empty_list(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        _, client = store_and_client
        r = client.get("/api/errors/recent")
        assert r.status_code == 200
        assert r.json() == []

    def test_excludes_successes(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _insert_conv(store, "ok", 1, "2024-01-01T10:00:00Z", "success")
        _insert_conv(store, "err", 2, "2024-01-01T11:00:00Z", "error", "timeout")
        r = client.get("/api/errors/recent")
        items = r.json()
        assert len(items) == 1
        assert items[0]["id"] == "err"

    def test_ordered_by_timestamp_desc(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _insert_conv(store, "e1", 1, "2024-01-01T10:00:00Z", "error", "timeout")
        _insert_conv(store, "e2", 2, "2024-01-02T10:00:00Z", "error", "timeout")
        _insert_conv(store, "e3", 3, "2024-01-03T10:00:00Z", "error", "timeout")
        items = client.get("/api/errors/recent").json()
        timestamps = [i["timestamp"] for i in items]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_limit_parameter(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        for i in range(10):
            _insert_conv(store, f"e{i}", i + 1, f"2024-01-0{i+1}T10:00:00Z", "error", "timeout")
        r = client.get("/api/errors/recent?limit=3")
        assert len(r.json()) == 3


class TestErrorsDaily:
    def test_empty_returns_empty(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        _, client = store_and_client
        r = client.get("/api/errors/daily")
        assert r.status_code == 200
        assert r.json() == []

    def test_daily_error_counts(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        # Use timestamps within the last 30 days window
        from datetime import datetime, timedelta, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

        _insert_conv(store, "e1", 1, f"{today}T10:00:00Z", "error", "timeout")
        _insert_conv(store, "e2", 2, f"{today}T11:00:00Z", "error", "rate_limit")
        _insert_conv(store, "e3", 3, f"{yesterday}T10:00:00Z", "error", "timeout")
        r = client.get("/api/errors/daily?days=30")
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1


class TestErrorsByType:
    def test_empty_returns_empty(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        _, client = store_and_client
        r = client.get("/api/errors/by-type")
        assert r.status_code == 200
        assert r.json() == []

    def test_groups_by_error_type(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _insert_conv(store, "e1", 1, f"{today}T10:00:00Z", "error", "timeout")
        _insert_conv(store, "e2", 2, f"{today}T11:00:00Z", "error", "timeout")
        _insert_conv(store, "e3", 3, f"{today}T12:00:00Z", "error", "rate_limit")
        r = client.get("/api/errors/by-type?days=30")
        assert r.status_code == 200
        data = r.json()
        types = {d["error_type"]: d["count"] for d in data}
        assert types.get("timeout") == 2
        assert types.get("rate_limit") == 1

    def test_row_has_expected_fields(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _insert_conv(store, "e1", 1, "2024-01-01T10:00:00Z", "error", "timeout")
        item = client.get("/api/errors/recent").json()[0]
        for field in ("id", "timestamp", "status"):
            assert field in item
