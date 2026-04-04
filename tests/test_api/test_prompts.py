"""Tests for prompt templates API endpoints."""
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


def _insert_template(store: AnalyticsStore, template_id: str, use_count: int = 1,
                     system_prompt: str = "You are a helper.") -> None:
    with store._get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO prompt_templates
               (template_id, system_prompt, first_seen, last_seen, use_count, total_cost_usd, avg_cost_usd)
               VALUES (?, ?, '2024-01-01T00:00:00Z', '2024-01-02T00:00:00Z', ?, 0.01, 0.01)""",
            (template_id, system_prompt, use_count),
        )


class TestListPromptTemplates:
    def test_empty_returns_empty_list(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        _, client = store_and_client
        r = client.get("/api/prompts/templates")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_lists_templates(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _insert_template(store, "tpl-1", use_count=5)
        _insert_template(store, "tpl-2", use_count=2)
        r = client.get("/api/prompts/templates")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_sorted_by_use_count_desc(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _insert_template(store, "tpl-a", use_count=1)
        _insert_template(store, "tpl-b", use_count=10)
        _insert_template(store, "tpl-c", use_count=5)
        r = client.get("/api/prompts/templates")
        items = r.json()["items"]
        counts = [i["use_count"] for i in items]
        assert counts == sorted(counts, reverse=True)

    def test_pagination(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        for i in range(1, 8):
            _insert_template(store, f"tpl-{i}", use_count=i)
        r1 = client.get("/api/prompts/templates?page=1&page_size=5")
        r2 = client.get("/api/prompts/templates?page=2&page_size=5")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["total"] == 7
        assert len(r1.json()["items"]) == 5
        assert len(r2.json()["items"]) == 2
        ids1 = {i["template_id"] for i in r1.json()["items"]}
        ids2 = {i["template_id"] for i in r2.json()["items"]}
        assert ids1.isdisjoint(ids2)

    def test_system_prompt_truncated_to_200_chars(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        long_prompt = "X" * 500
        _insert_template(store, "tpl-long", system_prompt=long_prompt)
        r = client.get("/api/prompts/templates")
        item = r.json()["items"][0]
        assert len(item["system_prompt_preview"]) <= 200

    def test_row_has_expected_fields(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _insert_template(store, "tpl-1")
        item = client.get("/api/prompts/templates").json()["items"][0]
        for field in ("template_id", "first_seen", "last_seen", "use_count",
                      "total_cost_usd", "avg_cost_usd"):
            assert field in item


class TestGetPromptTemplate:
    def test_get_existing(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _insert_template(store, "tpl-abc", use_count=3)
        r = client.get("/api/prompts/templates/tpl-abc")
        assert r.status_code == 200
        assert r.json()["template_id"] == "tpl-abc"

    def test_get_nonexistent_returns_404(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        _, client = store_and_client
        r = client.get("/api/prompts/templates/no-such-template")
        assert r.status_code == 404


class TestTemplateStats:
    def test_stats_with_conversations(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _insert_template(store, "tpl-1")
        # Insert some conversations for this template
        with store._get_conn() as conn:
            for i in range(3):
                conn.execute(
                    """INSERT INTO conversations
                       (id, seq, timestamp, template_id, status, duration_ms, cost_usd,
                        prompt_tokens, completion_tokens, total_tokens, finish_reason)
                       VALUES (?, ?, '2024-01-01T10:00:00Z', 'tpl-1', 'success', 100, 0.01,
                               100, 50, 150, 'stop')""",
                    (f"c-{i}", i + 1),
                )
        r = client.get("/api/prompts/templates/tpl-1/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total_conversations"] == 3
        assert data["success_rate"] == 1.0
        assert "quality_score" in data
        assert 0 <= data["quality_score"] <= 100

    def test_stats_nonexistent_template(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        _, client = store_and_client
        r = client.get("/api/prompts/templates/no-such/stats")
        assert r.status_code == 404


class TestTemplateDaily:
    def test_daily_trend(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _insert_template(store, "tpl-1")
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with store._get_conn() as conn:
            conn.execute(
                f"""INSERT INTO conversations
                   (id, seq, timestamp, template_id, status, cost_usd, duration_ms)
                   VALUES ('c1', 1, '{today}T10:00:00Z', 'tpl-1', 'success', 0.01, 100)""",
            )
        r = client.get("/api/prompts/templates/tpl-1/daily?days=30")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) >= 1


class TestTemplateConversations:
    def test_list_template_conversations(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _insert_template(store, "tpl-1")
        with store._get_conn() as conn:
            for i in range(5):
                conn.execute(
                    """INSERT INTO conversations
                       (id, seq, timestamp, template_id, status)
                       VALUES (?, ?, '2024-01-01T10:00:00Z', 'tpl-1', 'success')""",
                    (f"c-{i}", i + 1),
                )
        r = client.get("/api/prompts/templates/tpl-1/conversations?page_size=3")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 5
        assert len(data["items"]) == 3


class TestCompareTemplates:
    def test_compare_two_templates(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _insert_template(store, "tpl-a", use_count=10, system_prompt="Helper A")
        _insert_template(store, "tpl-b", use_count=5, system_prompt="Helper B")
        # Insert conversations
        with store._get_conn() as conn:
            conn.execute(
                "INSERT INTO conversations (id, seq, timestamp, template_id, status) VALUES ('ca', 1, '2024-01-01T10:00:00Z', 'tpl-a', 'success')")
            conn.execute(
                "INSERT INTO conversations (id, seq, timestamp, template_id, status) VALUES ('cb', 2, '2024-01-01T10:00:00Z', 'tpl-b', 'success')")
        r = client.get("/api/prompts/compare?template_a=tpl-a&template_b=tpl-b")
        assert r.status_code == 200
        data = r.json()
        assert "a" in data and "b" in data
        assert data["a"]["template_id"] == "tpl-a"
        assert data["b"]["template_id"] == "tpl-b"

    def test_compare_nonexistent(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _insert_template(store, "tpl-a")
        r = client.get("/api/prompts/compare?template_a=tpl-a&template_b=no-such")
        assert r.status_code == 404


class TestSimilarTemplates:
    def test_find_similar(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        store, client = store_and_client
        _insert_template(store, "tpl-1", system_prompt="You are a helpful coding assistant that writes Python code")
        _insert_template(store, "tpl-2", system_prompt="You are a helpful coding assistant that writes JavaScript code")
        _insert_template(store, "tpl-3", system_prompt="Completely different prompt about cooking recipes")
        r = client.get("/api/prompts/similar/tpl-1")
        assert r.status_code == 200
        data = r.json()
        # tpl-2 should be similar, tpl-3 probably not
        template_ids = [d["template_id"] for d in data]
        assert "tpl-2" in template_ids

    def test_similar_nonexistent(self, store_and_client: tuple[AnalyticsStore, TestClient]):
        _, client = store_and_client
        r = client.get("/api/prompts/similar/no-such")
        assert r.status_code == 404
