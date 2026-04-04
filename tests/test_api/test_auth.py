"""Tests for API key hash authentication."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from analyzer.store import AnalyticsStore
from tests.test_api.conftest import TEST_ADMIN_HASH, ADMIN_HEADERS

_RAW_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_requests (
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


def _init_raw_db(raw_db_path: str) -> None:
    conn = sqlite3.connect(raw_db_path)
    conn.executescript(_RAW_SCHEMA)
    conn.close()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App with ADMIN_KEY_HASH set (from autouse fixture)."""
    analytics_db = tmp_path / "analytics.db"
    raw_db = tmp_path / "raw.db"
    monkeypatch.setenv("ANALYTICS_DB", str(analytics_db))
    monkeypatch.setenv("RAW_DB", str(raw_db))
    monkeypatch.setenv("BODIES_DIR", str(tmp_path / "bodies"))
    AnalyticsStore(str(analytics_db))
    _init_raw_db(str(raw_db))
    return TestClient(create_app())


@pytest.fixture
def client_with_legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App with DASHBOARD_API_KEY=test-secret-key (legacy auth)."""
    analytics_db = tmp_path / "analytics.db"
    raw_db = tmp_path / "raw.db"
    monkeypatch.setenv("ANALYTICS_DB", str(analytics_db))
    monkeypatch.setenv("RAW_DB", str(raw_db))
    monkeypatch.setenv("BODIES_DIR", str(tmp_path / "bodies"))
    monkeypatch.setenv("DASHBOARD_API_KEY", "test-secret-key")
    AnalyticsStore(str(analytics_db))
    _init_raw_db(str(raw_db))
    return TestClient(create_app())


class TestKeyHashAuth:
    def test_no_auth_returns_401(self, client: TestClient):
        r = client.get("/api/overview")
        assert r.status_code == 401
        assert r.headers.get("WWW-Authenticate") == "Bearer"

    def test_admin_hash_bearer_returns_200(self, client: TestClient):
        r = client.get("/api/overview", headers=ADMIN_HEADERS)
        assert r.status_code == 200

    def test_admin_hash_query_returns_200(self, client: TestClient):
        r = client.get(f"/api/overview?key_hashes={TEST_ADMIN_HASH}")
        assert r.status_code == 200

    def test_non_admin_hash_returns_200(self, client: TestClient):
        r = client.get("/api/overview?key_hashes=abcdef01234567890abcdef012345678")
        assert r.status_code == 200

    def test_admin_endpoint_requires_admin(self, client: TestClient):
        """Non-admin hash should get 403 on admin endpoints."""
        r = client.get(
            "/api/admin/status",
            headers={"Authorization": "Bearer notadminhash00000000000000000000"},
        )
        assert r.status_code == 403

    def test_admin_endpoint_with_admin_hash(self, client: TestClient):
        r = client.get("/api/admin/status", headers=ADMIN_HEADERS)
        assert r.status_code == 200


class TestLegacyAuth:
    def test_missing_header_returns_401(self, client_with_legacy: TestClient):
        r = client_with_legacy.get("/api/overview")
        assert r.status_code == 401

    def test_wrong_token_returns_401(self, client_with_legacy: TestClient):
        r = client_with_legacy.get(
            "/api/overview",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert r.status_code == 401

    def test_correct_token_returns_200(self, client_with_legacy: TestClient):
        r = client_with_legacy.get(
            "/api/overview",
            headers={"Authorization": "Bearer test-secret-key"},
        )
        assert r.status_code == 200

    def test_static_files_not_blocked(self, client_with_legacy: TestClient):
        r = client_with_legacy.get("/")
        assert r.status_code != 401


class TestRateLimit:
    @pytest.fixture
    def limited_client(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        analytics_db = tmp_path / "analytics.db"
        raw_db = tmp_path / "raw.db"
        monkeypatch.setenv("ANALYTICS_DB", str(analytics_db))
        monkeypatch.setenv("RAW_DB", str(raw_db))
        monkeypatch.setenv("BODIES_DIR", str(tmp_path / "bodies"))
        monkeypatch.setenv("API_RATE_LIMIT_MAX_REQUESTS", "2")
        monkeypatch.setenv("API_RATE_LIMIT_WINDOW_SECONDS", "60")
        AnalyticsStore(str(analytics_db))
        _init_raw_db(str(raw_db))
        return TestClient(create_app())

    def test_rate_limit_blocks_excess_requests(self, limited_client: TestClient):
        headers = {"Authorization": "Bearer scopedhash0000000000000000000001"}
        assert limited_client.get("/api/overview", headers=headers).status_code == 200
        assert limited_client.get("/api/overview", headers=headers).status_code == 200

        blocked = limited_client.get("/api/overview", headers=headers)
        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"]

    def test_static_files_not_rate_limited(self, limited_client: TestClient):
        response = limited_client.get("/")
        assert response.status_code == 200
