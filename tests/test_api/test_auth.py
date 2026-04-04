"""Tests for optional API key authentication."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from analyzer.store import AnalyticsStore

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
def client_no_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App without DASHBOARD_API_KEY set — auth is disabled."""
    analytics_db = tmp_path / "analytics.db"
    raw_db = tmp_path / "raw.db"
    monkeypatch.setenv("ANALYTICS_DB", str(analytics_db))
    monkeypatch.setenv("RAW_DB", str(raw_db))
    monkeypatch.setenv("BODIES_DIR", str(tmp_path / "bodies"))
    monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    AnalyticsStore(str(analytics_db))
    _init_raw_db(str(raw_db))
    return TestClient(create_app())


@pytest.fixture
def client_with_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App with DASHBOARD_API_KEY=test-secret-key."""
    analytics_db = tmp_path / "analytics.db"
    raw_db = tmp_path / "raw.db"
    monkeypatch.setenv("ANALYTICS_DB", str(analytics_db))
    monkeypatch.setenv("RAW_DB", str(raw_db))
    monkeypatch.setenv("BODIES_DIR", str(tmp_path / "bodies"))
    monkeypatch.setenv("DASHBOARD_API_KEY", "test-secret-key")
    AnalyticsStore(str(analytics_db))
    _init_raw_db(str(raw_db))
    return TestClient(create_app())


class TestAuthDisabled:
    def test_api_accessible_without_token(self, client_no_auth: TestClient):
        r = client_no_auth.get("/api/overview")
        assert r.status_code == 200

    def test_api_accessible_with_any_token(self, client_no_auth: TestClient):
        r = client_no_auth.get("/api/overview", headers={"Authorization": "Bearer anything"})
        assert r.status_code == 200


class TestAuthEnabled:
    def test_missing_header_returns_401(self, client_with_auth: TestClient):
        r = client_with_auth.get("/api/overview")
        assert r.status_code == 401
        assert r.headers.get("WWW-Authenticate") == "Bearer"

    def test_wrong_token_returns_401(self, client_with_auth: TestClient):
        r = client_with_auth.get("/api/overview", headers={"Authorization": "Bearer wrong-key"})
        assert r.status_code == 401

    def test_correct_token_returns_200(self, client_with_auth: TestClient):
        r = client_with_auth.get(
            "/api/overview",
            headers={"Authorization": "Bearer test-secret-key"},
        )
        assert r.status_code == 200

    def test_non_bearer_scheme_returns_401(self, client_with_auth: TestClient):
        r = client_with_auth.get(
            "/api/overview",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert r.status_code == 401

    def test_admin_endpoint_also_protected(self, client_with_auth: TestClient):
        r = client_with_auth.get("/api/admin/status")
        assert r.status_code == 401

    def test_admin_endpoint_with_correct_token(self, client_with_auth: TestClient):
        r = client_with_auth.get(
            "/api/admin/status",
            headers={"Authorization": "Bearer test-secret-key"},
        )
        assert r.status_code == 200

    def test_static_files_not_blocked(self, client_with_auth: TestClient):
        """Static HTML served at / should not require auth."""
        r = client_with_auth.get("/")
        # Could be 200 or 404 depending on static dir presence in test env;
        # the key is that it does NOT return 401
        assert r.status_code != 401
