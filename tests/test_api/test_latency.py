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