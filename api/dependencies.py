from __future__ import annotations

import hmac
import os
import sqlite3
from typing import Generator

from fastapi import Header, HTTPException, status


def get_analytics_db() -> Generator[sqlite3.Connection, None, None]:
    db_path = os.getenv("ANALYTICS_DB", "/data/analytics/analytics.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_raw_db() -> Generator[sqlite3.Connection, None, None]:
    db_path = os.getenv("RAW_DB", "/data/logs/raw.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_bodies_dir() -> str:
    return os.getenv("BODIES_DIR", "/data/logs/bodies")


def verify_api_key(authorization: str | None = Header(default=None)) -> None:
    """Optional Bearer-token gate.

    When ``DASHBOARD_API_KEY`` env var is set every request to an API route
    must carry a matching ``Authorization: Bearer <key>`` header.
    When the env var is unset the check is skipped (backward compatible).
    """
    required_key = os.getenv("DASHBOARD_API_KEY", "").strip()
    if not required_key:
        return  # auth disabled

    token: str = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]

    if not token or not hmac.compare_digest(token, required_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
