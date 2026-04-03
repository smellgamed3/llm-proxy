"""Tests for models analytics API endpoints."""
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


def _seed(store: AnalyticsStore) -> None:
    with store._get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO daily_stats
               (date, model, provider, request_count, success_count, error_count,
                total_tokens, total_cost_usd, avg_duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("2024-01-01", "gpt-4o", "openai", 10, 9, 1, 1000, 0.10, 200.0),
                ("2024-01-02", "gpt-4o", "openai", 5, 5, 0, 500, 0.05, 180.0),
                ("2024-01-01", "claude-3-opus", "anthropic", 3, 3, 0, 300, 0.06, 300.0),
            ],
        )
        conn.executemany(
            """INSERT OR IGNORE INTO conversations
               (id, seq, timestamp, status, model)
               VALUES (?, ?, ?, ?, ?)""",
            [
                ("c1", 1, "2024-01-01T00:00:00Z", "success", "gpt-4o"),
                ("c2", 2, "2024-01-01T01:00:00Z", "success", "claude-3-opus"),
                ("c3", 3, "2024-01-02T00:00:00Z", "error", "gpt-4o"),
            ],
        )


class TestModelUsage:
    def test_empty_returns_empty_list(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        _, client = store_and_client
        r = client.get("/api/models/usage")
        assert r.status_code == 200
        assert r.json() == []

    def test_groups_by_model_and_provider(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed(store)
        r = client.get("/api/models/usage")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 2
        models = {item["model"] for item in items}
        assert models == {"gpt-4o", "claude-3-opus"}

    def test_sorted_by_request_count_desc(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed(store)
        r = client.get("/api/models/usage")
        counts = [item["request_count"] for item in r.json()]
        assert counts == sorted(counts, reverse=True)

    def test_aggregates_across_days(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed(store)
        r = client.get("/api/models/usage")
        gpt4o = next(i for i in r.json() if i["model"] == "gpt-4o")
        assert gpt4o["request_count"] == 15  # 10+5
        assert gpt4o["total_tokens"] == 1500  # 1000+500

    def test_date_from_filter(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed(store)
        r = client.get("/api/models/usage?date_from=2024-01-02")
        assert r.status_code == 200
        items = r.json()
        # Only 2024-01-02 has data, and only gpt-4o on that date
        assert len(items) == 1
        assert items[0]["model"] == "gpt-4o"
        assert items[0]["request_count"] == 5

    def test_date_to_filter(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed(store)
        r = client.get("/api/models/usage?date_to=2024-01-01")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 2
        gpt4o = next(i for i in items if i["model"] == "gpt-4o")
        assert gpt4o["request_count"] == 10  # only 2024-01-01

    def test_row_has_expected_fields(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed(store)
        r = client.get("/api/models/usage")
        for item in r.json():
            for field in ("model", "provider", "request_count", "success_count",
                          "error_count", "total_tokens", "cost_usd", "avg_duration_ms"):
                assert field in item


class TestModelList:
    def test_empty_db_returns_empty_list(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        _, client = store_and_client
        r = client.get("/api/models/list")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_distinct_model_names(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed(store)
        r = client.get("/api/models/list")
        assert r.status_code == 200
        models = r.json()
        assert isinstance(models, list)
        assert set(models) == {"gpt-4o", "claude-3-opus"}

    def test_no_duplicates(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed(store)
        r = client.get("/api/models/list")
        models = r.json()
        assert len(models) == len(set(models))

    def test_sorted_alphabetically(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _seed(store)
        r = client.get("/api/models/list")
        models = r.json()
        assert models == sorted(models)
