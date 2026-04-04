"""Shared fixtures for API tests."""
from __future__ import annotations

import pytest

TEST_ADMIN_HASH = "testadminhash000testadminhash00"
ADMIN_HEADERS = {"Authorization": f"Bearer {TEST_ADMIN_HASH}"}


@pytest.fixture(autouse=True)
def _set_admin_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-set ADMIN_KEY_HASH for all API tests."""
    monkeypatch.setenv("ADMIN_KEY_HASH", TEST_ADMIN_HASH)
    # Remove legacy key by default; individual tests can override
    monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
