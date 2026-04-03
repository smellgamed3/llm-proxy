"""Tests for latency analytics endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
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


def test_latency_summary_uses_p95(store_and_client: tuple[AnalyticsStore, TestClient]):
    store, client = store_and_client
    ts = datetime.now(timezone.utc).isoformat()
    for index, value in enumerate([10.0, 20.0, 30.0, 40.0, 100.0], start=1):
        store.upsert_conversation({
            "id": f"conv-{index}",
            "seq": index,
            "timestamp": ts,
            "duration_ms": value,
            "status": "success",
        })

    response = client.get("/api/latency/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["p50"] == 30.0
    assert data["p95"] == 100.0
    assert data["p99"] == 100.0


def test_latency_summary_empty_without_filters(store_and_client: tuple[AnalyticsStore, TestClient]):
    _, client = store_and_client
    response = client.get("/api/latency/summary")
    assert response.status_code == 200
    assert response.json()["count"] == 0


def test_latency_summary_includes_avg(store_and_client: tuple[AnalyticsStore, TestClient]):
    store, client = store_and_client
    ts = datetime.now(timezone.utc).isoformat()
    for index, value in enumerate([100.0, 200.0], start=1):
        store.upsert_conversation({
            "id": f"avg-{index}",
            "seq": index,
            "timestamp": ts,
            "duration_ms": value,
            "status": "success",
        })
    data = client.get("/api/latency/summary").json()
    assert data["avg"] == 150.0
    assert data["count"] == 2


def test_latency_summary_filter_by_model(store_and_client: tuple[AnalyticsStore, TestClient]):
    store, client = store_and_client
    ts = datetime.now(timezone.utc).isoformat()
    store.upsert_conversation({"id": "m1", "seq": 1, "timestamp": ts,
                               "duration_ms": 50.0, "status": "success", "model": "gpt-4o"})
    store.upsert_conversation({"id": "m2", "seq": 2, "timestamp": ts,
                               "duration_ms": 999.0, "status": "success", "model": "claude-3"})
    data = client.get("/api/latency/summary?model=gpt-4o").json()
    assert data["count"] == 1
    assert data["p50"] == 50.0


def test_latency_by_model_empty(store_and_client: tuple[AnalyticsStore, TestClient]):
    _, client = store_and_client
    response = client.get("/api/latency/by-model")
    assert response.status_code == 200
    assert response.json() == []


def test_latency_by_model_groups_correctly(store_and_client: tuple[AnalyticsStore, TestClient]):
    store, client = store_and_client
    ts = datetime.now(timezone.utc).isoformat()
    for i, (model, dur) in enumerate([("gpt-4o", 100.0), ("gpt-4o", 200.0), ("claude-3", 50.0)], start=1):
        store.upsert_conversation({
            "id": f"bm-{i}", "seq": i, "timestamp": ts,
            "duration_ms": dur, "status": "success", "model": model,
        })
    items = client.get("/api/latency/by-model").json()
    assert len(items) == 2
    gpt4o = next(i for i in items if i["model"] == "gpt-4o")
    assert gpt4o["avg_ms"] == 150.0
    assert gpt4o["count"] == 2


def test_latency_by_model_sorted_by_avg_desc(store_and_client: tuple[AnalyticsStore, TestClient]):
    store, client = store_and_client
    ts = datetime.now(timezone.utc).isoformat()
    for i, (model, dur) in enumerate([("fast-model", 10.0), ("slow-model", 900.0)], start=1):
        store.upsert_conversation({
            "id": f"ord-{i}", "seq": i, "timestamp": ts,
            "duration_ms": dur, "status": "success", "model": model,
        })
    items = client.get("/api/latency/by-model").json()
    avgs = [i["avg_ms"] for i in items]
    assert avgs == sorted(avgs, reverse=True)