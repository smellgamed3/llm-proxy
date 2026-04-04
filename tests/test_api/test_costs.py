"""Tests for cost analytics API endpoints."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from analyzer.store import AnalyticsStore
from tests.test_api.conftest import ADMIN_HEADERS


@pytest.fixture
def store_and_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[AnalyticsStore, TestClient]:
    analytics_db = tmp_path / "analytics.db"
    monkeypatch.setenv("ANALYTICS_DB", str(analytics_db))
    monkeypatch.setenv("RAW_DB", str(tmp_path / "raw.db"))
    monkeypatch.setenv("BODIES_DIR", str(tmp_path / "bodies"))
    store = AnalyticsStore(str(analytics_db))
    return store, TestClient(create_app(), headers=ADMIN_HEADERS)


def _seed_daily_stats(store: AnalyticsStore) -> None:
    """Seed daily_stats directly via store's internal connection."""
    with store._get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO daily_stats
               (date, model, provider, request_count, success_count, error_count,
                total_tokens, total_cost_usd, avg_duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("2024-01-01", "gpt-4o", "openai", 5, 5, 0, 500, 0.05, 100.0),
                ("2024-01-02", "gpt-4o", "openai", 3, 2, 1, 300, 0.03, 120.0),
                ("2024-01-03", "gpt-3.5-turbo", "openai", 10, 10, 0, 800, 0.008, 80.0),
            ],
        )


def _seed_conversations(store: AnalyticsStore) -> None:
    """Seed conversations matching the daily_stats profile.

    5 gpt-4o on 2024-01-01 (total_tokens=500, cost=0.05)
    3 gpt-4o on 2024-01-02 (total_tokens=300, cost=0.03)
    10 gpt-3.5-turbo on 2024-01-03 (total_tokens=800, cost=0.008)
    """
    seq = 0
    for count, model, date, tokens, cost in [
        (5, "gpt-4o", "2024-01-01", 500, 0.05),
        (3, "gpt-4o", "2024-01-02", 300, 0.03),
        (10, "gpt-3.5-turbo", "2024-01-03", 800, 0.008),
    ]:
        per_tok = tokens // count
        per_cost = cost / count
        for i in range(count):
            seq += 1
            store.upsert_conversation({
                "id": f"conv-{seq}",
                "seq": seq,
                "timestamp": f"{date}T{10 + i:02d}:00:00Z",
                "model": model,
                "status": "success",
                "cost_usd": per_cost,
                "total_tokens": per_tok,
                "duration_ms": 100.0,
            })


class TestCostsSummary:
    def test_empty_returns_zeros(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        _, client = store_and_client
        r = client.get("/api/costs/summary")
        assert r.status_code == 200
        data = r.json()
        assert data["total_cost_usd"] == 0.0
        assert data["total_tokens"] == 0
        assert data["total_requests"] == 0

    def test_sums_all_rows(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed_conversations(store)
        r = client.get("/api/costs/summary")
        assert r.status_code == 200
        data = r.json()
        assert data["total_requests"] == 18  # 5+3+10
        assert data["total_tokens"] == 1600  # 500+300+800
        assert abs(data["total_cost_usd"] - 0.088) < 1e-4

    def test_date_from_filter(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed_conversations(store)
        r = client.get("/api/costs/summary?date_from=2024-01-02")
        assert r.status_code == 200
        data = r.json()
        assert data["total_requests"] == 13  # 3+10

    def test_date_to_filter(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed_conversations(store)
        r = client.get("/api/costs/summary?date_to=2024-01-01")
        assert r.status_code == 200
        data = r.json()
        assert data["total_requests"] == 5

    def test_date_range_filter(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed_conversations(store)
        r = client.get("/api/costs/summary?date_from=2024-01-01&date_to=2024-01-02")
        assert r.status_code == 200
        data = r.json()
        assert data["total_requests"] == 8  # 5+3


class TestCostsByModel:
    def test_empty_returns_empty_list(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        _, client = store_and_client
        r = client.get("/api/costs/by-model")
        assert r.status_code == 200
        assert r.json() == []

    def test_groups_by_model(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed_conversations(store)
        r = client.get("/api/costs/by-model")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 2
        models = {item["model"] for item in items}
        assert models == {"gpt-4o", "gpt-3.5-turbo"}

    def test_sorted_by_cost_desc(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed_conversations(store)
        r = client.get("/api/costs/by-model")
        items = r.json()
        costs = [item["cost_usd"] for item in items]
        assert costs == sorted(costs, reverse=True)

    def test_date_filter_applied(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed_conversations(store)
        # Only 2024-01-03 has gpt-3.5-turbo
        r = client.get("/api/costs/by-model?date_from=2024-01-03&date_to=2024-01-03")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 1
        assert items[0]["model"] == "gpt-3.5-turbo"


class TestCostsDaily:
    def test_empty_returns_empty_list(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        _, client = store_and_client
        r = client.get("/api/costs/daily")
        assert r.status_code == 200
        assert r.json() == []

    def test_rows_ordered_by_date_asc(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed_daily_stats(store)
        r = client.get("/api/costs/daily?days=30")
        assert r.status_code == 200
        items = r.json()
        dates = [item["date"] for item in items]
        assert dates == sorted(dates)

    def test_each_row_has_expected_fields(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed_daily_stats(store)
        r = client.get("/api/costs/daily?days=30")
        for item in r.json():
            assert "date" in item
            assert "cost_usd" in item
            assert "total_tokens" in item
