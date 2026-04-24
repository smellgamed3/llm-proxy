"""Shared fixtures for API tests."""
from __future__ import annotations

import pytest


TEST_ADMIN_HASH = "testadminhash000testadminhash00"
ADMIN_HEADERS = {"Authorization": f"Bearer {TEST_ADMIN_HASH}"}


@pytest.fixture(autouse=True)
def _set_admin_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_KEY_HASH", TEST_ADMIN_HASH)
    monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    import api.dependencies as dep
    dep._pool = dep._ConnectionPool()
